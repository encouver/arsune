"""Queue claim checks, recover claims whose intake request stopped part way, and settle
claims whose check job stopped at its time or attempt limit."""

import logging
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select

from app.bounty.worker import proven_holder, settle_stopped_claim
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Claim, Job, LedgerEntry, PendingAction, utcnow

logger = logging.getLogger(__name__)

# Longer than a slow real bond transfer, including the NEAR outcome wait and RPC retries.
INTAKE_SECONDS = 300


def queue_check(session, claim, site_url):
    """Create the claim job once. A fault that is already proven is a duplicate at arrival."""
    if claim.job_id or claim.status != "received":
        return
    if holder := proven_holder(session, claim):
        claim.outcome = claim.status = "duplicate"
        claim.checker_reason = (
            f"Claim {holder} already proved this fault. No browser check was needed."
        )
    job_id = str(uuid4())
    session.add(
        Job(
            id=job_id,
            kind="claim",
            url=site_url,
            mode="scripted",
            deadline=utcnow() + timedelta(seconds=settings.job_deadline_seconds),
        )
    )
    session.flush()
    claim.job_id, claim.updated_at = job_id, utcnow()
    session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))


def recover_stuck_claims(now=None):
    """Resolve claims left in `received` without a job after the intake timeout.

    A recorded bond queues the check. A bond row without a transaction ID has an unknown
    result, so the claim waits for review and no transfer is repeated.
    """
    cutoff = (now or utcnow()) - timedelta(seconds=INTAKE_SECONDS)
    with Session.begin() as session:
        claims = session.scalars(
            select(Claim)
            .where(Claim.status == "received", Claim.job_id.is_(None), Claim.created_at < cutoff)
            .with_for_update(skip_locked=True)
        ).all()
        for claim in claims:
            bond = session.scalar(
                select(LedgerEntry)
                .where(LedgerEntry.claim_id == claim.id, LedgerEntry.kind == "bond_in")
                .with_for_update()
            )
            if bond and bond.tx_id:
                queue_check(session, claim, session.get(Bounty, claim.bounty_id).url)
                logger.info("Queued the check for recovered claim %s", claim.id)
                continue
            if bond:
                bond.status, bond.updated_at = "unknown", utcnow()
            claim.status, claim.updated_at = "needs_review", utcnow()
            claim.settlement_note = (
                "The bond transfer result is unknown after the intake stopped. "
                "Check the settlement account before any retry."
            )
            logger.warning("Claim %s needs review after an interrupted intake", claim.id)


def settle_stopped_claims():
    """Settle every claim whose job failed before its settlement ended.

    A claim in review with an outcome waits for an operator: a payment result is unknown. A
    claim in review without an outcome was held by an older rule that kept the bond after a
    timeout; it has no payment row, so its refund is safe.
    """
    with Session() as session:
        job_ids = session.scalars(
            select(Claim.job_id)
            .join(Job, Job.id == Claim.job_id)
            .where(
                Job.status == "failed",
                Claim.settled_at.is_(None),
                or_(Claim.status != "needs_review", Claim.outcome.is_(None)),
            )
        ).all()
    for job_id in job_ids:
        try:
            settle_stopped_claim(job_id)
            logger.info("Settled the claim of stopped job %s", job_id)
        except Exception:
            logger.exception("Settling the claim of stopped job %s failed", job_id)
