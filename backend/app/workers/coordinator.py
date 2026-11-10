import logging
import time
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.core.db import Session
from app.core.models import Job, PendingAction, utcnow
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
            if store.out_of_time(job, now):
                store.fail_job(session, job, now)
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
    from app.bounty.crosschain import poll_funding
    from app.bounty.recovery import recover_stuck_claims, settle_stopped_claims

    recover_stuck_claims(now)
    settle_stopped_claims()
    poll_funding(now)


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
