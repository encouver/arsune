"""Pure checks for zero-setup security claims, shared by the checker and the swarm.

No network and no provider calls: each function reads bytes or text that a caller fetched.
Personal data and secrets leave this module only masked.
"""

import base64
import json
import re
from urllib.parse import urlsplit

# Backend-as-a-service hosts that a site's own scripts name; data_exposure steps may use them.
BAAS_SUFFIXES = (".supabase.co", ".firebaseio.com")
BAAS_HOSTS = ("firestore.googleapis.com",)

EMAIL = re.compile(
    r"(?<![\w.+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}\b"
)
# A phone in free text needs a leading +; a phone under a phone-like JSON key may omit it.
PHONE_TEXT = re.compile(r"(?<![\w+])\+\d[\d\s().-]{7,20}\d(?!\w)")
PHONE_VALUE = re.compile(r"\+?\d[\d\s().-]{7,20}\d")
PHONE_KEYS = ("phone", "mobile", "tel")
# File-like "emails" such as logo@2x.png are not people.
NOT_EMAIL_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "js", "css", "ico", "avif"}
MIN_PHONE_DIGITS = 9
MAX_SAMPLES = 3

JWT = re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")
SECRET_PATTERNS = {
    "stripe_secret": re.compile(r"sk_live_[0-9A-Za-z]{20,}"),
    "aws_secret": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "openai_key": re.compile(r"(?<![A-Za-z0-9_-])sk-(?!ant-)(?:proj-)?[A-Za-z0-9_-]{20,}"),
    "anthropic_key": re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_-]{20,}"),
}
SECRET_KINDS = ("supabase_service_role", *SECRET_PATTERNS)
AWS_SECRET_VALUE = re.compile(r"(?<![A-Za-z0-9/+])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+])")
AWS_NEAR_CHARS = 300
PEM_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]{20,8000}?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)

FILE_KINDS = ("env", "git", "backup", "source_map")
ENV_LINE = re.compile(r"^(?:export\s+)?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
SQL_DUMP = re.compile(
    r"\b(?:CREATE TABLE|INSERT INTO)\s+(?:IF NOT EXISTS\s+)?[`\"]?([\w.]+)?", re.IGNORECASE
)
BACKUP_NAME = re.compile(r"backup|bak|dump|\bdb\b|database|sql|archive", re.IGNORECASE)
ARCHIVE_MAGIC = (b"PK\x03\x04", b"\x1f\x8b")


def baas_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return urlsplit(url).scheme == "https" and (
        host in BAAS_HOSTS or any(host.endswith(s) and host != s[1:] for s in BAAS_SUFFIXES)
    )


def mask(value: str) -> str:
    """b***@gmail.com for an email, +34*****12 for a phone, abcdef****wxyz for a secret."""
    value = str(value).strip()
    if "@" in value and EMAIL.fullmatch(value):
        name, domain = value.split("@", 1)
        return f"{name[:1]}***@{domain}"
    digits = re.sub(r"\D", "", value)
    if PHONE_VALUE.fullmatch(value) and len(digits) >= MIN_PHONE_DIGITS:
        plus = "+" if value.startswith("+") else ""
        return f"{plus}{digits[:2]}{'*' * (len(digits) - 4)}{digits[-2:]}"
    if len(value) <= 12:
        return value[:2] + "*" * 4
    return f"{value[:6]}****{value[-4:]}"


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _emails(text: str) -> set[str]:
    found = set()
    for match in EMAIL.finditer(text):
        email = match.group(0).lower().strip(".")
        if email.rsplit(".", 1)[-1] not in NOT_EMAIL_TLDS:
            found.add(email)
    return found


def _phones(text: str) -> set[str]:
    return {
        _digits(m.group(0))
        for m in PHONE_TEXT.finditer(text)
        if MIN_PHONE_DIGITS <= len(_digits(m.group(0))) <= 15
    }


def _walk(value, key=""):
    """(key, text) for every string or number leaf of a JSON value."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, str(k).lower())
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v, key)
    elif isinstance(value, (str, int)) and not isinstance(value, bool):
        yield key, str(value)


def _body_people(body: str) -> tuple[set[str], set[str]]:
    emails, phones = _emails(body), _phones(body)
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):
        return emails, phones
    for key, text in _walk(data):
        if any(word in key for word in PHONE_KEYS) and PHONE_VALUE.fullmatch(text.strip()):
            digits = _digits(text)
            if MIN_PHONE_DIGITS <= len(digits) <= 15:
                phones.add(digits)
    return emails, phones


def find_people(bodies: list[str], public_text: str, site_base: str) -> dict:
    """Count distinct people in response bodies, without data the site shows publicly.

    People are distinct emails, or distinct phones when there are more phones. Emails of the
    site's own domain and anything on its public pages do not count.
    """
    emails, phones = set(), set()
    for body in bodies:
        found_emails, found_phones = _body_people(body or "")
        emails |= found_emails
        phones |= found_phones
    public = (public_text or "").lower()
    public_digits = _phones(public_text or "") | {_digits(public)} - {""}
    base = (site_base or "").lower()

    def own(email):
        domain = email.rsplit("@", 1)[1]
        return bool(base) and (domain == base or domain.endswith("." + base))

    emails = {e for e in emails if e not in public and not own(e)}
    phones = {p for p in phones if not any(p in d for d in public_digits)}
    samples = [mask(e) for e in sorted(emails)[:MAX_SAMPLES]]
    samples += [mask("+" + p) for p in sorted(phones)[: MAX_SAMPLES - len(samples)]]
    return {
        "people": max(len(emails), len(phones)),
        "emails": len(emails),
        "phones": len(phones),
        "samples": samples,
    }


def _jwt_payload(token: str) -> dict | None:
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (ValueError, IndexError):
        return None
    return data if isinstance(data, dict) else None


def _mixed(value: str) -> bool:
    """Real API keys mix digits and letters; names like sk-loading-spinner-wrapper do not."""
    tail = value.split("-", 1)[-1]
    return any(c.isdigit() for c in tail) and any(c.isalpha() for c in tail)


def find_secret(body: str, kind: str) -> str | None:
    """The first value of this kind in the body, checked offline only; None when absent."""
    body = body or ""
    if kind == "supabase_service_role":
        return next(
            (
                m.group(0)
                for m in JWT.finditer(body)
                if (_jwt_payload(m.group(0)) or {}).get("role") == "service_role"
            ),
            None,
        )
    pattern = SECRET_PATTERNS.get(kind)
    if pattern is None:
        return None
    for match in pattern.finditer(body):
        value = match.group(0)
        if kind == "aws_secret":
            near = body[max(0, match.start() - AWS_NEAR_CHARS) : match.end() + AWS_NEAR_CHARS]
            if not any(m.group(0) != value for m in AWS_SECRET_VALUE.finditer(near)):
                continue
        elif kind == "private_key":
            block = PEM_BLOCK.match(body, match.start())
            return block.group(0) if block else body[match.start() : match.end() + 120]
        elif kind in ("openai_key", "anthropic_key") and not _mixed(value):
            continue
        return value
    return None


def _looks_html(text: str) -> bool:
    head = text.lstrip()[:512].lower()
    return head.startswith("<") or "<html" in head or "<!doctype" in head


def _text(body: bytes) -> str:
    return body.decode("utf-8", "replace")


def file_matches(body: bytes, kind: str, url: str) -> bool:
    """True when the body has the format of a private file of this kind."""
    body = body or b""
    path = urlsplit(url).path.lower()
    if kind == "backup" and body.startswith(ARCHIVE_MAGIC):
        return bool(BACKUP_NAME.search(path.rsplit("/", 1)[-1]))
    text = _text(body)
    if not text.strip() or _looks_html(text):
        return False
    if kind == "env":
        return len(ENV_LINE.findall(text)) >= 3
    if kind == "git":
        has_core = bool(re.search(r"^\s*\[core\]\s*$", text, re.MULTILINE))
        has_ref = text.startswith("ref: refs/")
        if path.endswith("/config"):
            return has_core
        if path.endswith("/head"):
            return has_ref
        return has_core or has_ref
    if kind == "backup":
        return bool(SQL_DUMP.search(text))
    if kind == "source_map":
        try:
            data = json.loads(text)
        except ValueError:
            return False
        if not isinstance(data, dict):
            return False
        sources = data.get("sourcesContent")
        return (
            data.get("version") == 3
            and isinstance(sources, list)
            and any(isinstance(s, str) and s.strip() for s in sources)
        )
    return False


def file_names(body: bytes, kind: str) -> list[str]:
    """Names from the matching lines (variables, tables, sources), never their values."""
    text = _text(body or b"")
    if kind == "env":
        names = ENV_LINE.findall(text)
    elif kind == "git":
        names = ["[core]"] if "[core]" in text else ["ref: refs/"] if "ref: refs/" in text else []
    elif kind == "backup":
        names = [m.group(1) for m in SQL_DUMP.finditer(text) if m.group(1)] or ["archive"]
    elif kind == "source_map":
        try:
            sources = json.loads(text).get("sources") or []
        except (ValueError, AttributeError):
            sources = []
        names = [s for s in sources if isinstance(s, str)]
    else:
        names = []
    return list(dict.fromkeys(names))[:5]
