import hashlib
import json
from datetime import timedelta

from sqlalchemy import select

from arsune.config import settings
from arsune.db import Attempt, Event, Job, PendingAction, Record, Session, utcnow


class LeaseLost(Exception):
    pass


def locked_job(session, job_id):
    return session.scalar(select(Job).where(Job.id == job_id).with_for_update())


def require_claim(job, generation):
    if (
        not job
        or job.status != "running"
        or job.generation != generation
        or not job.lease_until
        or job.lease_until <= utcnow()
    ):
        raise LeaseLost("Worker claim expired")


def claim(job_id):
    with Session.begin() as session:
        job = locked_job(session, job_id)
        if not job or job.status in ("completed", "failed"):
            return None
        now = utcnow()
        if job.status == "running" and job.lease_until and job.lease_until > now:
            return None
        if now >= job.deadline or job.generation >= settings.max_attempts:
            job.status, job.error = "failed", "The job reached its time or attempt limit."
            job.updated_at, job.lease_until = now, None
            job.progress = {"stage": "failed", "message": job.error}
            session.query(PendingAction).filter_by(job_id=job_id).delete()
            if job.generation:
                previous = session.get(Attempt, f"{job.id}:{job.generation}")
                if previous:
                    previous.status, previous.finished_at = "failed", now
            return None
        if job.generation:
            previous = session.get(Attempt, f"{job.id}:{job.generation}")
            if previous:
                previous.status, previous.finished_at = "expired", now
        job.generation += 1
        job.status, job.updated_at = "running", now
        job.lease_until = now + timedelta(seconds=settings.lease_seconds)
        session.add(
            Attempt(
                id=f"{job.id}:{job.generation}",
                job_id=job.id,
                generation=job.generation,
                status="running",
            )
        )
        return {
            "id": job.id,
            "url": job.url,
            "mode": job.mode,
            "inject_demo": job.inject_demo,
            "checkpoint": job.checkpoint,
            "generation": job.generation,
            "deadline": job.deadline,
        }


def heartbeat(job_id, generation):
    with Session.begin() as session:
        job = locked_job(session, job_id)
        require_claim(job, generation)
        if utcnow() >= job.deadline:
            raise LeaseLost("Job deadline reached")
        job.lease_until = utcnow() + timedelta(seconds=settings.lease_seconds)
        job.updated_at = utcnow()


def stable_id(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:32]


def persist_records(session, job_id, checkpoint):
    for kind, key in (
        ("observation", "observations"),
        ("candidate", "candidates"),
        ("finding", "findings"),
    ):
        values = checkpoint.get(key, checkpoint.get("checks", {}) if kind == "finding" else [])
        if isinstance(values, dict):
            values = list(values.values())
        for value in values:
            if not isinstance(value, dict):
                continue
            record_id = f"{job_id}:{kind}:{value.get('id') or stable_id(value)}"
            session.merge(Record(id=record_id, job_id=job_id, kind=kind, data=value))


def save_checkpoint(job_id, generation, checkpoint):
    with Session.begin() as session:
        job = locked_job(session, job_id)
        require_claim(job, generation)
        job.checkpoint, job.updated_at = checkpoint, utcnow()
        persist_records(session, job_id, checkpoint)


def emit_event(job_id, generation, role, message):
    with Session.begin() as session:
        job = locked_job(session, job_id)
        require_claim(job, generation)
        event_id = f"{job_id}:{stable_id([role, message])}"
        session.merge(Event(id=event_id, job_id=job_id, role=role, message=message[:2000]))
        job.progress = {"stage": role, "message": message[:2000]}
        job.updated_at = utcnow()


def finish(job_id, generation, report=None, error=None):
    with Session.begin() as session:
        job = locked_job(session, job_id)
        require_claim(job, generation)
        job.status = "failed" if error else "completed"
        job.error, job.report, job.updated_at = error, report, utcnow()
        job.progress = {"stage": job.status, "message": error or "Check complete"}
        job.lease_until = None
        attempt = session.get(Attempt, f"{job_id}:{generation}")
        attempt.status, attempt.finished_at = job.status, utcnow()
        if report:
            persist_records(session, job_id, report)
        session.query(PendingAction).filter_by(job_id=job_id).delete()
