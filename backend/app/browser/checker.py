import asyncio
import json
from urllib.parse import unquote, urlsplit

from playwright.async_api import Error as BrowserError

from app.browser.policy import origin
from app.browser.session import LOGIN_SECONDS, LoginFailed, login_session, replay_steps, scope_base
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


async def check_candidate(item, observe, context=None, access_test=None):
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
