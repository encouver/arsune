"""Claim scope rules and the fingerprint that decides duplicates."""

import hashlib
import json
import re
from urllib.parse import urljoin, urlsplit

from app.browser.policy import origin, validate_target

# Each supported claim type names its pages through exactly these detail keys.
CLAIM_TYPES = {
    "broken_link": ("source", "target"),
    "horizontal_overflow": ("url",),
    "price_mismatch": ("key",),
}
# NEAR account ID rules, which also accept 64-character implicit accounts.
ACCOUNT = re.compile(r"(?=.{2,64}$)([a-z\d]+[-_])*[a-z\d]+(\.([a-z\d]+[-_])*[a-z\d]+)*")
MAX_UNRESOLVED_CLAIMS = 3


class ClaimError(ValueError):
    pass


def fee_for(reward_amount: int, fee_bps: int) -> int:
    return reward_amount * fee_bps // 10_000


def fingerprint(claim_type: str, urls: list[str], details: dict) -> str:
    """Identify the fault, not the hunter, so equal claims from different hunters match."""
    value = json.dumps([claim_type, sorted(urls), details], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


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

    if claim_type == "price_mismatch":
        return fingerprint(claim_type, [land(u) for u in urls], details)
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
    details = {key: await on_site(details[key]) for key in keys}
    # Extra URLs would change the fingerprint without changing the fault.
    if sorted(urls) != sorted(details.values()):
        raise ClaimError("Claim URLs must be exactly the pages named in the details.")
    return urls, details
