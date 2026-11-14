"""Claim scope rules and the fingerprint that decides duplicates."""

import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit

from app.browser.checker import failing_images, matching_errors, normalize_message
from app.browser.policy import origin, validate_target

# Each supported claim type names its pages through exactly these detail keys.
CLAIM_TYPES = {
    "broken_link": ("source", "target"),
    "horizontal_overflow": ("url",),
    "price_mismatch": ("key",),
    "broken_image": ("page", "image"),
    "page_error": ("page", "message"),
    "access_leak": ("steps",),
}
# Claim types whose only page is details["page"]; the other key names what fails on it.
PAGE_FAULTS = ("broken_image", "page_error")
# NEAR account ID rules, which also accept 64-character implicit accounts.
ACCOUNT = re.compile(r"(?=.{2,64}$)([a-z\d]+[-_])*[a-z\d]+(\.([a-z\d]+[-_])*[a-z\d]+)*")
MAX_UNRESOLVED_CLAIMS = 3
MAX_STEPS = 6
MAX_STEP_BODY = 4096
STEP_PATH = re.compile(r"/(?!/)[^\s#\\]{0,2047}")


class ClaimError(ValueError):
    pass


def fee_for(reward_amount: int, fee_bps: int) -> int:
    return reward_amount * fee_bps // 10_000


def fingerprint(claim_type: str, urls: list[str], details: dict) -> str:
    """Identify the fault, not the hunter, so equal claims from different hunters match."""
    value = json.dumps([claim_type, sorted(urls), details], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def step_page(path: str) -> str:
    """The path of a step without its query string, with repeated and trailing slashes gone."""
    page = re.sub(r"/+", "/", path.split("?", 1)[0])
    return page.rstrip("/") or "/"


def access_fingerprint(marker_index: int, method: str, path: str) -> str:
    """One leak is one marker shown by one endpoint, whatever the query or the hunter."""
    value = json.dumps(["access_leak", marker_index, f"{method} {step_page(path)}"])
    return hashlib.sha256(value.encode()).hexdigest()


def scoped_steps(steps) -> list[dict]:
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ClaimError(f"An access claim needs 1 to {MAX_STEPS} steps.")
    clean = []
    for step in steps:
        if not isinstance(step, dict) or set(step) != {"method", "path", "body"}:
            raise ClaimError("Each step must contain exactly: method, path, body.")
        method, path, body = step["method"], step["path"], step["body"]
        if method not in ("GET", "POST"):
            raise ClaimError("A step method must be GET or POST.")
        if not isinstance(path, str) or not STEP_PATH.fullmatch(path):
            raise ClaimError("A step path must start with one / and name a path on the site.")
        if method == "GET" and body is not None:
            raise ClaimError("A GET step has no body.")
        if method == "POST" and not isinstance(body, dict):
            raise ClaimError("A POST step body must be a JSON object.")
        if body is not None and len(json.dumps(body)) > MAX_STEP_BODY:
            raise ClaimError(f"A step body must be at most {MAX_STEP_BODY} bytes of JSON.")
        clean.append({"method": method, "path": path, "body": body})
    return clean


def spell_url(parts) -> str:
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default_port = 443 if parts.scheme == "https" else 80
    if parts.port not in (None, default_port):
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}{urljoin('/', parts.path or '/')}"


async def canonical_url(url) -> str:
    """Apply the URL policy, then remove spelling differences that name the same page."""
    if not isinstance(url, str) or not 8 <= len(url) <= 2048:
        raise ClaimError("Each URL must be text of at most 2048 characters.")
    parts = urlsplit(await validate_target(url))
    if parts.query:
        raise ClaimError("Claim URLs must not contain query strings.")
    return spell_url(parts)


def final_fingerprint(claim_type: str, urls: list[str], details: dict, evidence: list[dict]):
    """Fingerprint the fault on the pages the browser reached after redirects.

    A claim on a redirecting alias then names the same fault as a claim on the final page.
    The query string and fragment are dropped, as they are at intake. For a broken link the
    source and the target are each replaced by their final page, so one fault is one final
    source page with one final target page, however many aliases lead to either.
    """
    final = {}
    for observation in evidence:
        reached = observation.get("final_url")
        if reached and urlsplit(reached).hostname:
            final[observation["url"]] = spell_url(urlsplit(reached))

    def land(url):
        return final.get(url, url)

    if claim_type == "access_leak":
        shown = next((o for o in evidence if o.get("marker") is not None), None)
        if shown:
            return access_fingerprint(shown["marker"], shown["method"], shown["path"])
        return fingerprint(claim_type, urls, details)
    if claim_type == "price_mismatch":
        return fingerprint(claim_type, [land(u) for u in urls], details)
    if claim_type in PAGE_FAULTS:
        # The fault is what failed on the final page: the image's final file, or the full
        # message of the matched error, so claims that quote parts of one message are one fault.
        page = next((o for o in evidence if o.get("url") == details["page"]), {})
        if claim_type == "broken_image":
            failing = failing_images(page, details["image"])
            found = [spell_url(urlsplit(image["final_url"])) for image in failing]
        else:
            matched = matching_errors(page, details["message"])
            found = [normalize_message(error["message"]) for error in matched]
        key = CLAIM_TYPES[claim_type][1]
        final_details = {"page": land(details["page"]), key: min(found, default=details[key])}
        return fingerprint(claim_type, [final_details["page"]], final_details)
    final_details = {key: land(url) for key, url in details.items()}
    return fingerprint(claim_type, list(final_details.values()), final_details)


async def scoped_claim(site_origin: str, claim_type: str, urls: list, details: dict):
    """Return canonical URLs and details that name one fault on the bounty site."""
    keys = CLAIM_TYPES.get(claim_type)
    if not keys:
        raise ClaimError("Supported claim types: " + ", ".join(CLAIM_TYPES) + ".")
    if not isinstance(details, dict) or set(details) != set(keys):
        raise ClaimError(f"Details for {claim_type} must contain exactly: {', '.join(keys)}.")

    async def on_site(url):
        value = await canonical_url(url)
        if origin(value) != site_origin:
            raise ClaimError("Every claim URL must be on the bounty site.")
        return value

    if claim_type == "access_leak":
        steps = scoped_steps(details["steps"])
        page = await on_site(site_origin + step_page(steps[-1]["path"]))
        # The query string of the last step may be part of the URL; the page is what counts.
        given = [u.split("?", 1)[0] if isinstance(u, str) else u for u in urls]
        if len(given) != 1 or step_page(urlsplit(await on_site(given[0])).path) != step_page(
            urlsplit(page).path
        ):
            raise ClaimError("The claim URL must be the site origin plus the last step path.")
        return [page], {"steps": steps}
    urls = [await on_site(u) for u in urls]
    if len(set(urls)) != len(urls):
        raise ClaimError("Claim URLs must not repeat.")
    if claim_type == "price_mismatch":
        key = details["key"]
        if not isinstance(key, str) or not 1 <= len(key) <= 500:
            raise ClaimError("The price key must be text of at most 500 characters.")
        if len(urls) != 2:
            raise ClaimError("A price mismatch compares exactly two pages.")
        return urls, {"key": key}
    if claim_type in PAGE_FAULTS:
        page = await on_site(details["page"])
        if urls != [page]:
            raise ClaimError("Claim URLs must be exactly the page named in the details.")
        if claim_type == "broken_image":
            return urls, {"page": page, "image": await on_site(details["image"])}
        message = normalize_message(details["message"])
        if not 8 <= len(message) <= 500:
            raise ClaimError("The error message must be text of 8 to 500 characters.")
        return urls, {"page": page, "message": message}
    details = {key: await on_site(details[key]) for key in keys}
    # Extra URLs would change the fingerprint without changing the fault.
    if sorted(urls) != sorted(details.values()):
        raise ClaimError("Claim URLs must be exactly the pages named in the details.")
    return urls, details
