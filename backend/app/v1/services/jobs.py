import re
from base64 import urlsafe_b64decode, urlsafe_b64encode
from binascii import Error as Base64Error
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, tuple_

from app.agents.candidates import validate_candidates
from app.browser.policy import TargetPolicyError, document_scope, origin, validate_target
from app.core.config import settings
from app.core.db import Session
from app.core.models import Event, Job, PendingAction, utcnow
from app.core.schemas.jobs import CreateJob, RerunJob

# Jobs are created with str(uuid4()); other text cannot name a job and may not reach the database.
JOB_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def find_job(session, job_id):
    job = session.get(Job, job_id) if JOB_ID.fullmatch(job_id) else None
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def detail(session, job):
    events = session.scalars(
        select(Event).where(Event.job_id == job.id).order_by(Event.created_at, Event.id)
    ).all()
    return {
        "id": job.id,
        "url": job.url,
        "kind": job.kind,
        "title": job.url,
        "baseline_job_id": job.baseline_job_id,
        "comparison_mapping": job.comparison_mapping,
        "mode": job.mode,
        "status": job.status,
        "progress": job.progress,
        "generation": job.generation,
        "events": [
            {"id": e.id, "role": e.role, "message": e.message, "created_at": e.created_at}
            for e in events
        ],
        "report": job.report,
        "findings": (job.report or {}).get("findings", []),
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


async def create_job(request: CreateJob):
    try:
        await validate_target(request.url, demo_origin=settings.demo_url)
    except (TargetPolicyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.mode == "model" and not settings.model_enabled:
        raise HTTPException(409, "Model mode needs a configured endpoint, model, and credential.")
    if request.demo_fault_injection and origin(request.url) != origin(settings.demo_url):
        raise HTTPException(422, "False candidates are limited to the controlled demo.")
    job_id = str(uuid4())
    with Session.begin() as session:
        job = Job(
            id=job_id,
            url=request.url,
            mode=request.mode,
            inject_demo=request.demo_fault_injection,
            deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
        )
        session.add(job)
        session.flush()
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
        return detail(session, job)


def list_jobs(limit: int = 10, cursor: str | None = None):
    columns = (
        Job.id,
        Job.url,
        Job.kind,
        Job.url.label("title"),
        Job.mode,
        Job.status,
        Job.baseline_job_id,
        Job.created_at,
        Job.updated_at,
    )
    # Claim and site checks belong to bounties and are shown there, not in the operator history.
    query = (
        select(*columns)
        .where(Job.kind.not_in(("claim", "site_check")))
        .order_by(Job.created_at.desc(), Job.id.desc())
    )
    if cursor is not None:
        try:
            timestamp, job_id = urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            created_at = datetime.fromisoformat(timestamp)
            if created_at.tzinfo is None or str(UUID(job_id)) != job_id:
                raise ValueError("Invalid cursor")
        except (ValueError, UnicodeError, Base64Error):
            raise HTTPException(422, "Invalid history cursor") from None
        query = query.where(tuple_(Job.created_at, Job.id) < tuple_(created_at, job_id))
    with Session() as session:
        rows = session.execute(query.limit(limit + 1)).mappings().all()
        items = [dict(row) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = items[-1]
        value = f"{last['created_at'].isoformat()}|{last['id']}"
        next_cursor = urlsafe_b64encode(value.encode()).decode()
    return {"items": items, "next_cursor": next_cursor}


def get_job(job_id: str):
    with Session() as session:
        return detail(session, find_job(session, job_id))


async def rerun_job(baseline_id: str, request: RerunJob):
    with Session() as session:
        baseline = find_job(session, baseline_id)
        if baseline.kind != "website":
            raise HTTPException(422, "Incident checks do not support website reruns.")
        if baseline.status != "completed" or not baseline.report:
            raise HTTPException(409, "The baseline must have a completed report.")
        report = baseline.report

        def normalized(value):
            parts = urlsplit(value)
            return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))

        if (
            normalized(report.get("site_url", "")) != normalized(baseline.url)
            or not isinstance(report.get("inspected_pages"), list)
            or not isinstance(report.get("confirmed"), list)
            or len(report["inspected_pages"]) > 5
        ):
            raise HTTPException(409, "The baseline report has no valid saved scope.")
        selected = report["inspected_pages"]
        try:
            if (
                not selected
                or len(set(selected)) != len(selected)
                or any(
                    not isinstance(u, str) or not document_scope(baseline.url, u) for u in selected
                )
            ):
                raise ValueError("Invalid baseline page scope")
            claims = report["confirmed"]
            if any(
                not isinstance(f, dict)
                or not isinstance(f.get("id"), str)
                or f.get("status") != "confirmed"
                for f in claims
            ):
                raise ValueError("Invalid baseline claims")
            if validate_candidates(deepcopy(claims), selected)[1]:
                raise ValueError("Invalid baseline claims")
        except (ValueError, TypeError, KeyError):
            raise HTTPException(409, "The baseline has invalid saved pages or claims.") from None
        url, mapping = baseline.url, {}
        if request.demo_corrected:
            source = settings.demo_url.rstrip("/") + "/faulty/"
            target = settings.demo_url.rstrip("/") + "/corrected/"
            if url != source or any(not u.startswith(source) for u in report["inspected_pages"]):
                raise HTTPException(422, "This mapping requires the exact controlled faulty demo.")
            url, mapping = target, {source: target}
        mode, inject = baseline.mode, baseline.inject_demo
    try:
        await validate_target(url, demo_origin=settings.demo_url)
    except (TargetPolicyError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if mode == "model" and not settings.model_enabled:
        raise HTTPException(409, "Model mode needs a configured endpoint, model, and credential.")
    job_id = str(uuid4())
    with Session.begin() as session:
        job = Job(
            id=job_id,
            url=url,
            mode=mode,
            inject_demo=inject,
            baseline_job_id=baseline_id,
            comparison_mapping=mapping,
            deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
        )
        session.add(job)
        session.flush()
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
        return detail(session, job)
