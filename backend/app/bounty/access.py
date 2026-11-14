"""The owner's security test: test account A and the secret markers that A must never see.

Arsune makes the markers when it creates the bounty. The owner plants them in data that A must
not reach and gives the login of A. Markers appear only in the owner view, never in a public
response, a claim, a log line or evidence.
"""

import hashlib
import hmac
import secrets
from urllib.parse import urlsplit

from app import demo_shop
from app.browser.policy import TargetPolicyError, validate_target
from app.browser.session import in_scope, scope_base

MARKER_LABELS = (
    "other user's data",
    "data table row",
    "admin-only data",
    "chatbot instructions",
)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MAX_FIELD = 256
SETUP_FIRST = "Set up the security test first (step 2)."


def new_markers() -> list[str]:
    return [_new_code() for _ in MARKER_LABELS]


def _new_code() -> str:
    """KX-xxxx-xxxx-xxxx-xxxx: 80 random bits that look like an ordinary reference code."""
    chars = [secrets.choice(_CODE_ALPHABET) for _ in range(16)]
    return "KX-" + "-".join("".join(chars[i : i + 4]) for i in range(0, 16, 4))


def initial(site_url: str | None) -> dict:
    """The demo shop has a known test account; other sites set A later. Pass the demo URL only."""
    if site_url:
        parts = urlsplit(site_url)
        return demo_shop.access_test(f"{parts.scheme}://{parts.netloc}")
    return {"account": None, "markers": new_markers()}


def account(access_test: dict | None) -> dict | None:
    """Test account A: login_url, email, password. Older demo rows stored a JSON login step."""
    access_test = access_test or {}
    if access_test.get("account"):
        return access_test["account"]
    return None


def ready(access_test: dict | None) -> bool:
    return bool(account(access_test) and (access_test or {}).get("markers"))


def public_view(access_test: dict | None) -> dict | None:
    """What an agent needs to log in as A (a test account). No marker."""
    if not ready(access_test):
        return None
    test_account = account(access_test)
    return {key: test_account[key] for key in ("login_url", "email", "password")}


def owner_view(access_test: dict | None) -> dict:
    markers = (access_test or {}).get("markers") or []
    test_account = account(access_test) or {}
    return {
        "ready": ready(access_test),
        "login_url": test_account.get("login_url"),
        "email": test_account.get("email"),
        "password": test_account.get("password"),
        "markers": [
            {
                "index": n + 1,
                "label": MARKER_LABELS[n] if n < len(MARKER_LABELS) else "",
                "value": m,
            }
            for n, m in enumerate(markers)
        ],
    }


async def checked_account(site_url: str, login_url, email, password) -> dict:
    """Return test account A or raise ValueError with a plain reason."""
    if not isinstance(login_url, str) or not in_scope(login_url, scope_base(host(site_url))):
        raise ValueError("The login page must be a full URL on the bounty site or its subdomains.")
    try:
        login_url = await validate_target(login_url)
    except TargetPolicyError as exc:
        raise ValueError(str(exc)) from None
    for name, value in (("email", email), ("password", password)):
        if not isinstance(value, str) or not 1 <= len(value) <= MAX_FIELD:
            raise ValueError(f"The {name} must be text of 1 to {MAX_FIELD} characters.")
    return {"login_url": login_url, "email": email, "password": password}


def host(url: str) -> str:
    return urlsplit(url).hostname or ""


def new_owner_key() -> tuple[str, str]:
    key = secrets.token_urlsafe(24)
    return key, key_hash(key)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def owner_key_matches(stored_hash: str | None, key: str | None) -> bool:
    return bool(stored_hash and key) and hmac.compare_digest(stored_hash, key_hash(key))
