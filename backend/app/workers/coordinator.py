import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.db import Session
from app.core.models import Attempt, Job, PendingAction, utcnow
from app.workers import store
from app.workers.queue import app

logger = logging.getLogger(__name__)


def tick():
    now = utcnow()
    with Session.begin() as session:
        jobs = session.scalars(
            select(Job)
            .where(Job.status.in_(["queued", "running"]))
            .with_for_update(skip_locked=True)
        ).all()
        for job in jobs:
            if job.deadline <= now or (
                job.generation >= settings.max_attempts
                and job.lease_until
                and job.lease_until <= now
            ):
                job.status, job.error = "failed", "The job reached its time or attempt limit."
                job.updated_at = now
                job.lease_until = None
                job.progress = {"stage": "failed", "message": job.error}
                action = session.get(PendingAction, f"{job.id}:scan")
                if action:
                    session.delete(action)
                if job.generation:
                    attempt = session.get(Attempt, f"{job.id}:{job.generation}")
                    if attempt:
                        attempt.status, attempt.finished_at = "failed", now
                store.hold_unsettled_claim(session, job)
                continue
            if job.status == "queued" or not job.lease_until or job.lease_until <= now:
                action_id = f"{job.id}:scan"
                if not session.get(PendingAction, action_id):
                    session.add(PendingAction(id=action_id, job_id=job.id, publish_after=now))
        actions = session.scalars(
            select(PendingAction)
            .where(PendingAction.publish_after <= now)
            .with_for_update(skip_locked=True)
        ).all()
        for action in actions:
            job = session.get(Job, action.job_id)
            if not job or job.status in ("completed", "failed"):
                session.delete(action)
                continue
            if job.status == "running" and job.lease_until and job.lease_until > now:
                continue
            # Publish then defer within the transaction. A crash can repeat delivery;
            # the database claim makes that repeat safe.
            app.send_task("arsune.scan", args=[action.job_id])
            action.publish_after = now + timedelta(seconds=settings.lease_seconds)


def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            tick()
        except Exception:
            logger.exception("Coordinator cycle failed")
        time.sleep(settings.coordinator_interval)


if __name__ == "__main__":
    main()
