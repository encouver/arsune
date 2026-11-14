import asyncio
import hashlib
import json
from urllib.parse import unquote, urljoin, urlsplit

from playwright.async_api import Error as BrowserError

from app.bounty import security_checks
from app.browser.policy import TargetPolicyError, origin, validate_target
from app.browser.session import (
    LOGIN_SECONDS,
    MAX_STEP_REDIRECTS,
    LoginFailed,
    in_scope,
    login_session,
    replay_steps,
    scope_base,
)
from app.core.scan import VIEWPORTS


def image_state(image):
    """Return broken, loads, or unknown for one image from a fresh page read."""
    status = image.get("status")
    if status is not None and status >= 400:
        return "broken"
    if image.get("decoded") is True:
        return "loads"
    if (
        image.get("decoded") is False
        and status
        and 200 <= status < 300
        and not image.get("failure")
    ):
        return "broken"
    return "unknown"


def image_key(url):
    """Name an image file by origin and path; the query string and fragment do not count."""
    try:
        return origin(url) + (urlsplit(url).path or "/")
    except (TypeError, ValueError):
        return None


def matching_images(observation, image_url):
    key = image_key(image_url)
    return [
        image
        for image in observation.get("images", [])
        if key and key in {image_key(image["src"]), image_key(image["final_url"])}
    ]


def failing_images(observation, image_url):
    """Images that match the claimed URL, fail, and are shown to visitors."""
    return [
        image
        for image in matching_images(observation, image_url)
        if image.get("rendered") and image_state(image) == "broken"
    ]


def normalize_message(message):
    return " ".join(message.split()).casefold() if isinstance(message, str) else ""


def matching_errors(observation, message, first_party=True):
    """Uncaught errors whose message contains the claimed text of at least 8 characters."""
    wanted = normalize_message(message)
    if len(wanted) < 8:
        return []
    return [
        error
        for error in observation.get("page_errors", [])
        if bool(error.get("first_party")) == first_party
        and wanted in normalize_message(error.get("message"))
    ]


def load_reproduced(observation):
    """Arsune's own limits blocked nothing that a visitor's browser would load.

    Data requests that another site's script sends to another site do not count (see
    app.browser.observations.mark_third_party_data).
    """
    return not any(
        issue["resource_type"] != "Readiness"
        and not issue.get("http_status")
        and not issue.get("third_party_data")
        for issue in observation.get("resource_issues", [])
    )


def load_complete(observation):
    """Every resource loaded, except images that the site answered with an HTTP error and
    data requests that another site's script sends to another site."""
    return not any(
        (issue["resource_type"] != "Image" or not issue.get("http_status"))
        and not issue.get("third_party_data")
        for issue in observation.get("resource_issues", [])
    )


def check_broken_image(page, image_url):
    images = matching_images(page, image_url)
    states = [image_state(image) for image in images]
    failing = failing_images(page, image_url)
    if failing:
        image = failing[0]
        if image["status"] >= 400:
            return "confirmed", (
                f"A fresh page read found the image. Its request returned HTTP {image['status']}."
            )
        return "confirmed", (
            f"A fresh page read found the image. The server returned HTTP {image['status']}, "
            "but the browser could not decode the image."
        )
    if images and all(state == "loads" for state in states):
        return "rejected", "A fresh page read found the image, and the browser loaded it."
    if "broken" in states:
        return "unverified", "The image fails, but the page does not show it to visitors."
    if images:
        return "unverified", "The image did not finish loading, or Arsune's limits blocked it."
    return "unverified", "A fresh page read did not find this image on the page."


def check_page_error(page, message):
    if len(normalize_message(message)) < 8:
        return "unverified", "The claimed error message needs at least 8 characters."
    matched = matching_errors(page, message)
    if matched and load_reproduced(page):
        error = matched[0]
        kind = "promise rejection" if error["kind"] == "rejection" else "error"
        return "confirmed", f"A fresh page load raised an uncaught {kind}: {error['message']}"
    if matched:
        return "unverified", (
            "A matching error occurred, but Arsune's limits blocked resources that a "
            "visitor's browser would load."
        )
    if matching_errors(page, message, first_party=False):
        return "unverified", (
            "The matching error comes from a script on another origin, which this check "
            "does not cover."
        )
    if any(error.get("first_party") for error in page.get("page_errors", [])):
        return "unverified", "The fresh page load raised a different uncaught error."
    if load_complete(page) and not page.get("page_errors_truncated"):
        return "rejected", "A complete fresh page load raised no uncaught error from the site."
    return "unverified", "The page did not load completely, so the error cannot be ruled out."


STEP_FAILED = (BrowserError, TimeoutError, ValueError)
QUOTE_CHARS = 80
NO_LEAK = "No step showed data that account A must not see."
NO_TEST = (
    "This bounty has no security test (test account A and markers), so Arsune cannot check "
    "an access claim."
)


def marker_quote(text, marker, markers):
    """At most QUOTE_CHARS characters around the marker, with every marker hidden."""
    text = text.replace(marker, "\x01")
    for other in markers:
        text = text.replace(other, "[MARKER]")
    at = text.find("\x01")
    side = (QUOTE_CHARS - len("[MARKER]")) // 2
    quote = text[max(0, at - side) : at] + "[MARKER]" + text[at + 1 : at + 1 + side]
    return " ".join(quote.replace("\x01", "[MARKER]").split())


def echoed_markers(steps, markers):
    """Markers that a step sends itself; a site that echoes its input proves nothing."""
    sent = unquote(json.dumps(steps)).casefold()
    return {n for n, marker in enumerate(markers) if marker.casefold() in sent}


async def check_access_leak(item, context, access_test):
    """Log in as account A in a real browser, replay the steps, and look for the markers."""
    test_account = (access_test or {}).get("account")
    markers = (access_test or {}).get("markers") or []
    if not test_account or not markers or context is None:
        return "unverified", NO_TEST, []
    steps = item.get("details", {}).get("steps") or []
    site_origin = origin(item["urls"][0])
    base = scope_base(urlsplit(test_account["login_url"]).hostname or "")
    echoed = echoed_markers(steps, markers)
    browser = context.browser
    try:
        async with asyncio.timeout(LOGIN_SECONDS * 3):
            session = await login_session(
                browser,
                test_account["login_url"],
                test_account["email"],
                test_account["password"],
                base,
            )
    except LoginFailed as exc:
        return "unverified", f"Arsune could not log in as test account A: {exc}", []
    except STEP_FAILED:
        return "unverified", "Arsune could not log in as test account A.", []
    results = await replay_steps(browser, session, steps, base, site_origin)
    evidence = []
    for number, (step, result) in enumerate(zip(steps, results, strict=True), 1):
        parts = urlsplit(result["url"])
        shown_path = parts.path + (f"?{parts.query}" if parts.query else "")
        record = {
            "url": result["url"],
            "method": step["method"],
            "path": shown_path,
            "status": result["status"],
            "marker": None,
        }
        if result["status"] is None:
            record["error"] = result.get("error") or "The step did not finish."
            evidence.append(record)
            continue
        text = result["body"]
        shown = next((n for n, m in enumerate(markers) if n not in echoed and m in text), None)
        if shown is not None:
            record["marker"] = shown + 1
            record["quote"] = marker_quote(text, markers[shown], markers)
            evidence.append(record)
            reason = (
                f"Step {number} ({step['method']} {shown_path}) returned HTTP "
                f"{record['status']} with marker {shown + 1} of {len(markers)}, data that "
                f'account A must not see: "{record["quote"]}"'
            )
            return "confirmed", reason, evidence
        evidence.append(record)
    if any(r["status"] is None for r in evidence):
        return "unverified", "A step did not finish, so the leak cannot be ruled out.", evidence
    return "rejected", NO_LEAK, evidence


async def check_candidate(item, observe, context=None, access_test=None, site_url=None):
    if item["claim_type"] in SECURITY_CHECKS:
        status, reason, evidence = await check_security(item, context, site_url)
        return security_result(item, status, reason, evidence)
    if item["claim_type"] == "access_leak":
        status, reason, evidence = await check_access_leak(item, context, access_test)
        return access_result(item, status, reason, evidence)
    fresh = [
        await observe(
            u,
            VIEWPORTS["mobile"]
            if item["claim_type"] == "horizontal_overflow"
            else VIEWPORTS["desktop"],
        )
        for u in item["urls"]
    ]
    status, reason = (
        "unverified",
        "The supplied claim could not be checked with the supported measurements.",
    )
    details = item.get("details", {})
    if all(o.get("status") is not None for o in fresh):
        if item["claim_type"] == "broken_link":
            source = next((o for o in fresh if o["url"] == details.get("source")), None)
            target = next((o for o in fresh if o["url"] == details.get("target")), None)
            if (
                source
                and target
                and 200 <= source["status"] < 300
                and source.get("layout_complete", True)
                and target["url"] in source["links"]
            ):
                status = (
                    "confirmed"
                    if target["status"] >= 400
                    else "rejected"
                    if 200 <= target["status"] < 300 and target.get("layout_complete", True)
                    else "unverified"
                )
                reason = f"A fresh source-page read found the link. A fresh target request returned HTTP {target['status']}."
        elif item["claim_type"] == "horizontal_overflow":
            if 200 <= fresh[0]["status"] < 300 and fresh[0].get("layout_complete", True):
                width = fresh[0].get("scroll_width", 0)
                status = "confirmed" if width > 392 else "rejected"
                reason = (
                    f"Fresh measurement: content width {width} pixels; viewport width 390 pixels."
                )
        elif (
            item["claim_type"] == "price_mismatch"
            and len(fresh) == 2
            and all(200 <= o["status"] < 300 and o.get("layout_complete", True) for o in fresh)
        ):
            values = [
                [f["value"] for f in o["facts"] if f["key"] == details.get("key")] for o in fresh
            ]
            facts = [[f for f in o["facts"] if f["key"] == details.get("key")] for o in fresh]
            ordinary_complete = not any(
                f.get("source") == "visible_price"
                and not all(
                    f.get(field) for field in ("context", "quote", "selector", "screenshot")
                )
                for group in facts
                for f in group
            )
            same_context = len(facts[0]) == len(facts[1]) == 1 and facts[0][0].get(
                "context"
            ) == facts[1][0].get("context")
            if all(len(v) == 1 for v in values) and ordinary_complete and same_context:
                status = "confirmed" if values[0] != values[1] else "rejected"
                fact = next(f for f in fresh[0]["facts"] if f["key"] == details["key"])
                context = fact.get("context")
                label = (
                    f"{context['name']}, {context['currency']}, per {context['period']}, unit {context['unit']}"
                    if context
                    else details["key"]
                )
                reason = f"Fresh page reads for {label}: {values[0][0]} and {values[1][0]}."
        elif item["claim_type"] in {"broken_image", "page_error"}:
            page = next((o for o in fresh if o["url"] == details.get("page")), None)
            if page and 200 <= page["status"] < 300 and not page.get("error"):
                if item["claim_type"] == "broken_image":
                    status, reason = check_broken_image(page, details.get("image"))
                else:
                    status, reason = check_page_error(page, details.get("message"))
    if item["claim_type"] == "horizontal_overflow" and any(
        not o.get("layout_complete", True) for o in fresh
    ):
        status, reason = (
            "unverified",
            "Required page resources were unavailable or did not become ready.",
        )
    result = {
        **item,
        "status": status,
        "checker_reason": reason,
        "evidence": fresh,
        "visitor_impact": {
            "broken_image": "Visitors see an empty space or a broken image instead of the picture.",
            "page_error": "A script on this page stops with an error, so parts of the page can "
            "fail for visitors.",
        }.get(
            item["claim_type"],
            "Visitors can have difficulty using this page or choosing the stated offer.",
        ),
        "suggested_correction": {
            "broken_link": "Repair the link or restore the destination page.",
            "horizontal_overflow": "Make the content fit the mobile viewport.",
            "price_mismatch": "Use the same value for the same product and billing context.",
            "broken_image": "Restore the image file or correct the image address.",
            "page_error": "Correct the script so that the page loads without an uncaught error.",
        }.get(item["claim_type"], "Review the supplied reproduction steps."),
    }
    if status == "rejected":
        result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
        result["suggested_correction"] = "No repair is needed for this tested claim."
    elif status == "unverified":
        result["visitor_impact"] = "The effect on visitors is not known."
        result["suggested_correction"] = "Collect more evidence before deciding on a repair."
    return result


def access_result(item, status, reason, evidence):
    result = {
        **item,
        "status": status,
        "checker_reason": reason,
        "evidence": evidence,
        "visitor_impact": "A logged-in user can read data that belongs to someone else or "
        "that only the site's staff should see.",
        "suggested_correction": "Check on the server that the logged-in user may read each "
        "record, table row, admin answer, and bot instruction before you return it.",
    }
    if status == "rejected":
        result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
        result["suggested_correction"] = "No repair is needed for this tested claim."
    elif status == "unverified":
        result["visitor_impact"] = "The effect on visitors is not known."
        result["suggested_correction"] = "Collect more evidence before deciding on a repair."
    return result


# ------------------------------------------------------------- zero-setup security claims
# A fresh, anonymous browser context replays what the claim names. Personal data and secrets
# reach the evidence only masked; files only by the names on their matching lines.

SECURITY_SECONDS = 10
EXPOSURE_BYTES = 512 * 1024
SECRET_BYTES = 2_000_000
FILE_BYTES = 1_000_000
PUBLIC_PAGE_BYTES = 512 * 1024
MIN_PEOPLE = 3
SECRET_LABELS = {
    "supabase_service_role": "a Supabase service_role key",
    "stripe_secret": "a live Stripe secret key",
    "aws_secret": "an AWS access key with its secret",
    "private_key": "a private key",
    "openai_key": "an OpenAI API key",
    "anthropic_key": "an Anthropic API key",
}
FILE_LABELS = {
    "env": "an environment (.env) file",
    "git": "a Git repository file",
    "backup": "a backup or database dump",
    "source_map": "a source map with the original source code",
}


class OutOfScope(ValueError):
    pass


async def _security_allowed(url, base, site_origin, baas):
    in_site = in_scope(url, base) or origin(url) == origin(site_origin)
    if not (in_site or (baas and security_checks.baas_host(url))):
        return False
    try:
        await validate_target(url)
    except TargetPolicyError:
        return False
    return True


async def anonymous_fetch(context, step, base, site_origin, max_bytes, baas=False):
    """One request without login; redirects are followed only inside the scope."""
    url, method, body = step["url"], step["method"], step.get("body")
    headers = step.get("headers") or {}
    for _ in range(MAX_STEP_REDIRECTS + 1):
        if not await _security_allowed(url, base, site_origin, baas):
            raise OutOfScope("A request or its redirect left the bounty site.")
        options = {"method": method, "max_redirects": 0, "timeout": SECURITY_SECONDS * 1000}
        if headers:
            options["headers"] = headers
        if method == "POST" and body is not None:
            options["data"] = body
        response = await context.request.fetch(url, **options)
        try:
            location = response.headers.get("location")
            if response.status in (301, 302, 303, 307, 308) and location:
                url = urljoin(url, location)
                if response.status in (301, 302, 303):
                    method, body = "GET", None
                continue
            length = int(response.headers.get("content-length") or 0)
            raw = b"" if length > max_bytes else (await response.body())[:max_bytes]
            return url, response.status, raw
        finally:
            await response.dispose()
    raise OutOfScope("A request redirected too many times.")


async def _run(context, step, base, site_origin, max_bytes, baas=False):
    """{url, method, status, body} or {url, method, status: None, error}."""
    record = {"url": step["url"], "method": step["method"], "status": None}
    try:
        async with asyncio.timeout(SECURITY_SECONDS + 1):
            record["url"], record["status"], body = await anonymous_fetch(
                context, step, base, site_origin, max_bytes, baas
            )
        return record, body
    except OutOfScope as exc:
        record["error"] = str(exc)
    except (BrowserError, TimeoutError, ValueError):
        record["error"] = "The request did not finish."
    return record, b""


async def _public_text(context, urls, base, site_origin):
    """Text of the site's public home pages; data shown there is not a leak."""
    texts = []
    for url in dict.fromkeys(urls):
        step = {"url": url, "method": "GET"}
        record, body = await _run(context, step, base, site_origin, PUBLIC_PAGE_BYTES, True)
        if record["status"] and 200 <= record["status"] < 400:
            texts.append(body.decode("utf-8", "replace"))
    return "\n".join(texts)


def _home(url):
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}/"


async def check_data_exposure(item, context, base, site_origin, site_url):
    steps = item["details"]["steps"]
    evidence, bodies = [], []
    for step in steps:
        record, body = await _run(context, step, base, site_origin, EXPOSURE_BYTES, baas=True)
        evidence.append(record)
        bodies.append(body.decode("utf-8", "replace"))
    if all(r["status"] is None for r in evidence):
        return "unverified", "No step finished, so the exposure cannot be ruled out.", evidence
    pages = [_home(site_url), site_url, _home(steps[-1]["url"])]
    public = await _public_text(context, pages, base, site_origin)
    found = security_checks.find_people(bodies, public, base)
    evidence.append({"url": steps[-1]["url"], "kind": "data_exposure", **found})
    shown = ", ".join(found["samples"])
    counts = f"{found['emails']} emails and {found['phones']} phone numbers"
    if found["people"] >= MIN_PEOPLE:
        reason = (
            f"Without login, the steps returned personal data of {found['people']} people "
            f"({counts}) that the site's public pages do not show. Masked samples: {shown}."
        )
        return "confirmed", reason, evidence
    if any(r.get("status") is None for r in evidence[:-1]):
        return "unverified", "A step did not finish, so the exposure cannot be ruled out.", evidence
    reason = (
        f"Without login, the steps returned personal data of {found['people']} people "
        f"({counts}); at least {MIN_PEOPLE} are needed."
    )
    return "rejected", reason, evidence


async def check_secret_key(item, context, base, site_origin, site_url):
    url, kind = item["details"]["url"], item["details"]["kind"]
    record, body = await _run(
        context, {"url": url, "method": "GET"}, base, site_origin, SECRET_BYTES
    )
    record["kind"] = kind
    if record["status"] is None:
        return "unverified", record.get("error", "The file did not load."), [record]
    value = security_checks.find_secret(body.decode("utf-8", "replace"), kind)
    if not value or not 200 <= record["status"] < 300:
        reason = f"The file at {url} (HTTP {record['status']}) holds no {SECRET_LABELS[kind]}."
        return "rejected", reason, [record]
    record["masked"] = security_checks.mask(value)
    record["value_sha256"] = hashlib.sha256(value.encode()).hexdigest()[:16]
    reason = (
        f"Anyone can load {url} (HTTP {record['status']}). It holds "
        f"{SECRET_LABELS[kind]}: {record['masked']}."
    )
    return "confirmed", reason, [record]


async def check_private_file(item, context, base, site_origin, site_url):
    url, kind = item["details"]["url"], item["details"]["kind"]
    record, body = await _run(context, {"url": url, "method": "GET"}, base, site_origin, FILE_BYTES)
    record["kind"] = kind
    if record["status"] is None:
        return "unverified", record.get("error", "The file did not load."), [record]
    if not 200 <= record["status"] < 300 or not security_checks.file_matches(body, kind, url):
        reason = f"{url} (HTTP {record['status']}) is not {FILE_LABELS[kind]}."
        return "rejected", reason, [record]
    record["size"] = len(body)
    record["names"] = security_checks.file_names(body, kind)
    names = ", ".join(record["names"])
    reason = (
        f"Anyone can load {url} (HTTP {record['status']}, {len(body)} bytes). It is "
        f"{FILE_LABELS[kind]}" + (f"; it names: {names}." if names else ".")
    )
    return "confirmed", reason, [record]


SECURITY_CHECKS = {
    "data_exposure": check_data_exposure,
    "secret_key": check_secret_key,
    "private_file": check_private_file,
}


async def check_security(item, context, site_url=None):
    if context is None:
        return "unverified", "No browser was available for the check.", []
    site_url = site_url or item["urls"][0]
    site_origin = _home(site_url).rstrip("/")
    base = scope_base(urlsplit(site_origin).hostname or "")
    fresh = await context.browser.new_context(service_workers="block", accept_downloads=False)
    try:
        return await SECURITY_CHECKS[item["claim_type"]](item, fresh, base, site_origin, site_url)
    finally:
        await fresh.close()


SECURITY_IMPACT = {
    "data_exposure": (
        "Anyone, without an account, can download personal data of the site's users.",
        (
            "Turn on row level security or a server-side check so that each request returns only "
            "the data of the logged-in user, and remove columns that visitors must not see."
        ),
    ),
    "secret_key": (
        (
            "Anyone who reads the site's public code gets a key with full access to a paid or "
            "private service."
        ),
        "Revoke and replace the key now, then keep secret keys on the server only.",
    ),
    "private_file": (
        "Anyone can download a file that holds the site's settings, source code or data.",
        (
            "Remove the file from the public web folder or block it in the server configuration, "
            "and replace any secret it contained."
        ),
    ),
}


def security_result(item, status, reason, evidence):
    impact, correction = SECURITY_IMPACT[item["claim_type"]]
    result = {
        **item,
        "status": status,
        "checker_reason": reason,
        "evidence": evidence,
        "visitor_impact": impact,
        "suggested_correction": correction,
    }
    if status == "rejected":
        result["visitor_impact"] = "The fresh check did not reproduce this claimed fault."
        result["suggested_correction"] = "No repair is needed for this tested claim."
    elif status == "unverified":
        result["visitor_impact"] = "The effect on visitors is not known."
        result["suggested_correction"] = "Collect more evidence before deciding on a repair."
    return result
