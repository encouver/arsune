from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.browser.policy import TargetPolicyError, origin, validate_target
from app.core.config import settings
from app.core.db import Session
from app.core.models import Event, Job, PendingAction, utcnow
from app.core.schemas.jobs import CreateJob


def detail(session, job):
    events = session.scalars(
        select(Event).where(Event.job_id == job.id).order_by(Event.created_at, Event.id)
    ).all()
    return {
        "id": job.id,
        "url": job.url,
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


def get_job(job_id: str):
    with Session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return detail(session, job)
