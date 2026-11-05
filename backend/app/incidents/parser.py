"""Bounded parsing of inert, job-owned incident sources."""

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta

MAX_BYTES = 10 * 1024 * 1024
EVENT_FIELDS = {
    "id",
    "timestamp",
    "service",
    "level",
    "message",
    "request_id",
    "error_code",
    "revision",
    "column",
}
DEPLOY_FIELDS = {"id", "timestamp", "service", "revision", "migration_status", "migration_column"}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def timestamp(value, location):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)", value
    ):
        raise ValueError(f"{location}: timestamp needs an explicit UTC offset.")
    try:
        if value[-1] != "Z" and (int(value[-5:-3]) > 23 or int(value[-2:]) > 59):
            raise ValueError("invalid UTC offset")
        parsed = datetime.fromisoformat(value)
        normalized = parsed.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        raise ValueError(f"{location}: invalid timestamp.") from None
    return normalized


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field {key}.")
        result[key] = value
    return result


def _json(text, location):
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid JSON number")),
        )
    except (ValueError, RecursionError) as exc:
        raise ValueError(f"{location}: invalid JSON ({str(exc)[:160]}).") from None


def _record(item, fields, required, location):
    if not isinstance(item, dict):
        raise ValueError(f"{location}: record must be a JSON object.")  # noqa: TRY004
    if set(item) - fields:
        raise ValueError(f"{location}: unknown fields: {repr(sorted(set(item) - fields))[:200]}.")
    if required - set(item):
        raise ValueError(f"{location}: missing fields: {', '.join(sorted(required - set(item)))}.")
    for key, value in item.items():
        if isinstance(value, str):
            _safe_text(value, location)
        limit = 4000 if key == "message" else 200
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(
                f"{location}: {key} must be nonempty text, at most {limit} characters."
            )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,199}", item["id"]):
        raise ValueError(f"{location}: id must be a stable identifier.")
    return {**item, "timestamp": timestamp(item["timestamp"], location)}


def _safe_text(value, location):
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{location}: text must be valid UTF-8.") from None
    if "\x00" in value:
        raise ValueError(f"{location}: NUL characters are not permitted.")


def validate_bundle(description, service, start, end, events_text, deployments_text):
    for name, value, limit in [("description", description, 2000), ("service", service, 200)]:
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError(f"{name} must be nonempty text, at most {limit} characters.")
        _safe_text(value, name)
    start, end = timestamp(start, "start"), timestamp(end, "end")
    duration = datetime.fromisoformat(end) - datetime.fromisoformat(start)
    if duration <= timedelta(0) or duration > timedelta(hours=24):
        raise ValueError("The selected window must be greater than zero and at most 24 hours.")
    if not isinstance(events_text, str) or not isinstance(deployments_text, str):
        raise ValueError("Both sources must be text.")  # noqa: TRY004
    try:
        size = len(events_text.encode("utf-8")) + len(deployments_text.encode("utf-8"))
    except UnicodeEncodeError:
        raise ValueError("Sources must contain valid UTF-8 text.") from None
    if size > MAX_BYTES:
        raise ValueError("Sources exceed the combined 10 MB limit.")
    events, ids = [], set()
    for line, raw in enumerate(events_text.splitlines(), 1):
        location = f"events.ndjson line {line}"
        if not raw.strip():
            raise ValueError(f"{location}: empty records are not permitted.")
        item = _record(
            _json(raw, location),
            EVENT_FIELDS,
            {"id", "timestamp", "service", "level", "message"},
            location,
        )
        if item["level"] not in {"debug", "info", "warning", "error", "critical"}:
            raise ValueError(f"{location}: unknown level.")
        if item["id"] in ids:
            raise ValueError(f"{location}: duplicate event id {item['id']}.")
        ids.add(item["id"])
        events.append({**item, "_line": line})
        if len(events) > 5000:
            raise ValueError(f"{location}: more than 5000 events.")
    deployments = _json(deployments_text, "deployments.json line 1")
    if not isinstance(deployments, list) or len(deployments) > 20:
        raise ValueError("deployments.json line 1: expected an array of at most 20 records.")
    normalized, ids = [], set()
    for index, raw in enumerate(deployments, 1):
        location = f"deployments.json record {index}"
        item = _record(
            raw,
            DEPLOY_FIELDS,
            {"id", "timestamp", "service", "revision", "migration_status"},
            location,
        )
        if item["migration_status"] not in {"failed", "completed", "not_required"}:
            raise ValueError(f"{location}: unknown migration_status.")
        if item["id"] in ids:
            raise ValueError(f"{location}: duplicate deployment id {item['id']}.")
        ids.add(item["id"])
        normalized.append({**item, "_record": index})
    sources = {
        name: {"text": text, "sha256": digest(text)}
        for name, text in [("events.ndjson", events_text), ("deployments.json", deployments_text)]
    }
    return {
        "description": description,
        "service": service,
        "start": start,
        "end": end,
        "sources": sources,
        "events": events,
        "deployments": normalized,
        "raw_bytes": size,
    }
