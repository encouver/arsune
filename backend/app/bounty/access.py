"""The owner's security test: test account A and the secret markers that A must never see.

Arsune makes the markers when it creates the bounty. The owner plants them in data that A must
not reach and gives the login of A. Markers appear only in the owner view, never in a public
response, a claim, a log line or evidence.
"""

import hashlib
import hmac
import json
import secrets

from app import demo_shop
from app.bounty.claims import MAX_STEP_BODY, STEP_PATH

MARKER_LABELS = (
    "other user's data",
    "data table row",
    "admin-only data",
    "chatbot instructions",
)
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
SETUP_FIRST = "Set up the security test first (step 2)."


def new_markers() -> list[str]:
    return [_new_code() for _ in MARKER_LABELS]


def _new_code() -> str:
    """KX-xxxx-xxxx-xxxx-xxxx: 80 random bits that look like an ordinary reference code."""
    chars = [secrets.choice(_CODE_ALPHABET) for _ in range(16)]
    return "KX-" + "-".join("".join(chars[i : i + 4]) for i in range(0, 16, 4))


def initial(is_demo: bool) -> dict:
    """The demo shop plants fixed markers and has a known test account; other sites set A later."""
    return demo_shop.access_test() if is_demo else {"login": None, "markers": new_markers()}


def ready(access_test: dict | None) -> bool:
    return bool(access_test and access_test.get("login") and access_test.get("markers"))


def public_view(access_test: dict | None) -> dict | None:
    """What an agent needs to log in as A. No marker."""
    if not ready(access_test):
        return None
    login = access_test["login"]
    return {"login_path": login["path"], "login_body": login["body"]}


def owner_view(access_test: dict | None) -> dict:
    markers = (access_test or {}).get("markers") or []
    login = (access_test or {}).get("login")
    return {
        "ready": ready(access_test),
        "login_path": login["path"] if login else None,
        "login_body": login["body"] if login else None,
        "markers": [
            {
                "index": n + 1,
                "label": MARKER_LABELS[n] if n < len(MARKER_LABELS) else "",
                "value": m,
            }
            for n, m in enumerate(markers)
        ],
    }


def checked_login(path, body) -> dict:
    """Return the login step or raise ValueError with a plain reason."""
    if not isinstance(path, str) or not STEP_PATH.fullmatch(path):
        raise ValueError("The login path must start with one / and name a path on the site.")
    if not isinstance(body, dict) or not body:
        raise ValueError("The login body must be a non-empty JSON object.")
    if len(json.dumps(body)) > MAX_STEP_BODY:
        raise ValueError(f"The login body must be at most {MAX_STEP_BODY} bytes of JSON.")
    return {"path": path, "body": body}


def new_owner_key() -> tuple[str, str]:
    key = secrets.token_urlsafe(24)
    return key, key_hash(key)


def key_hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def owner_key_matches(stored_hash: str | None, key: str | None) -> bool:
    return bool(stored_hash and key) and hmac.compare_digest(stored_hash, key_hash(key))
