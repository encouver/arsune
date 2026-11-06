"""Accept bounded local incident files and serve verified source records."""

import json
from datetime import timedelta
from html import escape
from uuid import uuid4

from fastapi import HTTPException

from app.core.config import settings
from app.core.db import Session
from app.core.models import IncidentSource, Job, PendingAction, utcnow
from app.incidents.parser import deployment_texts, digest
from app.v1.services.jobs import detail, find_job

FILENAMES = {"events.ndjson", "deployments.json"}
MAX_SOURCE_BYTES = 10 * 1024 * 1024
# JSON can escape every source byte as six ASCII bytes; decoded files have a separate cap.
MAX_REQUEST_BYTES = MAX_SOURCE_BYTES * 6 + 64 * 1024


def create_incident(payload):
    from app.incidents import validate_bundle

    if (
        not isinstance(payload, dict)
        or set(payload)
        != {"description", "service", "start", "end", "events_text", "deployments_text"}
        or any(not isinstance(v, str) for v in payload.values())
    ):
        raise HTTPException(
            422,
            "Supply description, service, start, end, events_text and deployments_text as text.",
        )
    try:
        for value in payload.values():
            value.encode("utf-8")
            if "\x00" in value:
                raise HTTPException(422, "Incident text must not contain null characters.")
    except UnicodeEncodeError:
        raise HTTPException(422, "Incident text must use valid Unicode.") from None
    if (
        sum(len(payload[k].encode("utf-8")) for k in ("events_text", "deployments_text"))
        > MAX_SOURCE_BYTES
    ):
        raise HTTPException(413, "Incident source files exceed 10 MB.")
    try:
        validate_bundle(**payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    job_id = str(uuid4())
    with Session.begin() as session:
        job = Job(
            id=job_id,
            kind="incident",
            url=f"Incident: {payload['service']}",
            mode="scripted",
            incident_input={
                key: payload[key] for key in ("description", "service", "start", "end")
            },
            deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
        )
        session.add(job)
        session.flush()
        for filename, key in (
            ("events.ndjson", "events_text"),
            ("deployments.json", "deployments_text"),
        ):
            text = payload[key]
            session.add(
                IncidentSource(
                    job_id=job_id,
                    filename=filename,
                    sha256=digest(text),
                    content=text,
                )
            )
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
        return detail(session, job)


def load_sources(job_id):
    with Session() as session:
        sources = session.query(IncidentSource).filter_by(job_id=job_id).all()
        if {s.filename for s in sources} != FILENAMES:
            raise ValueError("Incident source files are unavailable.")
        result = {}
        for source in sources:
            if digest(source.content) != source.sha256:
                raise ValueError("Incident source hash does not match.")
            result[source.filename] = source.content
        return result


def source_evidence(job_id, filename, record_id=None, line=None):
    if filename not in FILENAMES:
        raise HTTPException(404, "Incident source not found.")
    if (record_id is None) == (line is None):
        raise HTTPException(422, "Supply one record_id or line selector.")
    with Session() as session:
        if find_job(session, job_id).kind != "incident":
            raise HTTPException(422, "This job has no incident sources.")
    try:
        sources = load_sources(job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    text = sources[filename]
    if filename == "events.ndjson":
        texts = text.splitlines()
    else:
        if line is not None:
            raise HTTPException(422, "Select deployments by record_id.")
        texts = deployment_texts(text)
    for index, raw in enumerate(texts, 1):
        record = json.loads(raw)
        if (record_id is not None and record.get("id") == record_id) or (
            line is not None and index == line
        ):
            return {
                "filename": filename,
                "sha256": digest(text),
                "record_id": record["id"],
                "line": index if filename == "events.ndjson" else None,
                "record_index": index,
                "record": record,
                "raw": raw,
                "excerpt": escape(raw),
            }
    raise HTTPException(404, "Incident source record not found.")
