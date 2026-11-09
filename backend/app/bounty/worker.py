"""Check a hunter claim with fresh browser evidence, then settle its money at most once.

Settlement runs inside the claim job, so the job lease and generation rules protect it. A
ledger row is committed before each outgoing payment. A repeated run that finds such a row
without a transaction ID does not pay again: the claim goes to review instead.
"""

import asyncio
import logging
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.bounty import settlement
from app.bounty.claims import fee_for
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.session import inspection_browser
from app.core.db import Session
from app.core.models import Bounty, Claim, LedgerEntry, utcnow
from app.settlement.base import SettlementError
from app.workers import store

logger = logging.getLogger(__name__)

CHECK_SECONDS = 120
OUTCOMES = {"confirmed": "proven", "rejected": "rejected", "unverified": "unverified"}


def locked_claim(session, job_id):
    return session.scalar(select(Claim).where(Claim.job_id == job_id).with_for_update())


def locked_bounty(session, bounty_id):
    return session.scalar(select(Bounty).where(Bounty.id == bounty_id).with_for_update())


def lock_bounty_then_claim(session, job_id):
    """Lock in one order everywhere: bounty, then claim, then ledger row.

    A claim that becomes proven can wait for another claim of the same bounty through the
    unique index, so taking the bounty first prevents a lock cycle.
    """
    bounty_id = session.scalar(select(Claim.bounty_id).where(Claim.job_id == job_id))
    bounty = locked_bounty(session, bounty_id)
    return bounty, locked_claim(session, job_id)


def start_check(job_id, generation):
    with Session.begin() as session:
        store.require_claim(store.locked_job(session, job_id), generation)
        claim = locked_claim(session, job_id)
        if claim.status == "received":
            claim.status, claim.updated_at = "checking", utcnow()
        item = {
            "id": claim.id,
            "role": "hunter",
            "claim_type": claim.claim_type,
            "urls": claim.urls,
            "details": claim.details,
            "explanation": claim.explanation,
            "reproduction_steps": claim.reproduction_steps,
        }
        return item, session.get(Bounty, claim.bounty_id).url


async def check_claim(job_id, generation, checkpoint, save_checkpoint, output_dir):
    if "result" in checkpoint:
        return checkpoint["result"]
    item, site_url = start_check(job_id, generation)
    from app.core.config import settings

    state = {"selected": list(item["urls"])}

    async def keep():
        # Only the finished result is saved; an interrupted check starts again.
        pass

    try:
        async with asyncio.timeout(CHECK_SECONDS):
            async with inspection_browser(site_url, settings.demo_url, state, keep) as (
                context,
                blocked,
            ):

                async def observe(target, viewport):
                    return await observe_page(context, target, viewport, output_dir, site_url)

                result = await check_candidate(item, observe)
        result["coverage"] = {
            "blocked_requests": blocked,
            "redirects": state.get("redirects", []),
            "resource_issues": state.get("resource_issues", []),
        }
    except store.LeaseLost:
        raise
    except Exception:
        # A check that cannot finish proves nothing; the bond is refunded as unverified.
        logger.exception("Claim check for job %s did not complete", job_id)
        result = {
            **item,
            "status": "unverified",
            "checker_reason": "The fresh check could not complete.",
            "evidence": [],
        }
    save_checkpoint({"result": result})
    return result


def evidence_summary(result):
    keys = ("url", "final_url", "viewport", "status", "captured_at", "screenshot")
    return [
        {key: o.get(key) for key in keys} | {"layout_complete": o.get("layout_complete", True)}
        for o in result.get("evidence", [])
    ]


def entry(bounty, claim, kind, amount, memo, mode, status, tx_id=None):
    return LedgerEntry(
        id=str(uuid4()),
        bounty_id=bounty.id,
        claim_id=claim.id,
        kind=kind,
        amount=amount,
        account=claim.hunter_account if kind != "fee" else "arsune",
        memo=memo,
        tx_id=tx_id,
        settlement_mode=mode,
        status=status,
    )


def decide(job_id, generation, result, mode):
    """Save the outcome once. The partial unique index lets only one claim hold proven."""
    with Session.begin() as session:
        store.require_claim(store.locked_job(session, job_id), generation)
        bounty, claim = lock_bounty_then_claim(session, job_id)
        if claim.outcome:
            return
        outcome = OUTCOMES.get(result.get("status"), "unverified")
        if outcome == "proven":
            try:
                with session.begin_nested():
                    claim.outcome = "proven"
            except IntegrityError:
                outcome = "duplicate"
        now = utcnow()
        claim.outcome = claim.status = outcome
        claim.checker_reason = result.get("checker_reason")
        claim.evidence = evidence_summary(result)
        claim.updated_at = now
        if outcome == "rejected":
            session.add(
                entry(
                    bounty,
                    claim,
                    "bond_forfeit",
                    bounty.bond_amount,
                    f"arsune:forfeit:{claim.id}",
                    mode,
                    "completed",
                )
            )
            bounty.budget_amount += bounty.bond_amount
            bounty.updated_at = now
            claim.settlement_note = "The bond was added to the bounty budget."
            claim.settled_at = now


def review(claim, note):
    claim.status, claim.settlement_note, claim.updated_at = "needs_review", note, utcnow()


def reserve_payment(job_id, generation, mode):
    """Commit the payment row before the transfer; return the transfer to send, if any."""
    with Session.begin() as session:
        store.require_claim(store.locked_job(session, job_id), generation)
        bounty, claim = lock_bounty_then_claim(session, job_id)
        if claim.settled_at or claim.status == "needs_review":
            return None
        kind = "reward_out" if claim.outcome == "proven" else "bond_refund"
        previous = session.scalar(
            select(LedgerEntry).where(LedgerEntry.claim_id == claim.id, LedgerEntry.kind == kind)
        )
        if previous:
            review(
                claim,
                "A payment attempt has no recorded result. "
                "Check the settlement account before any retry.",
            )
            return None
        if kind == "bond_refund":
            memo = f"arsune:refund:{claim.id}"
            amount = recorded = bounty.bond_amount
        else:
            fee = fee_for(bounty.reward_amount, bounty.fee_bps)
            if bounty.budget_amount < bounty.reward_amount + fee:
                review(claim, "The bounty budget cannot pay this reward. The bond is held.")
                return None
            bounty.budget_amount -= bounty.reward_amount + fee
            if bounty.budget_amount < bounty.reward_amount + fee:
                bounty.status = "closed"
            bounty.updated_at = utcnow()
            if fee:
                session.add(
                    entry(bounty, claim, "fee", fee, f"arsune:fee:{claim.id}", mode, "completed")
                )
            # One transfer returns the reward and the bond; the ledger records both parts.
            memo = f"arsune:reward:{claim.id}"
            amount, recorded = bounty.reward_amount + bounty.bond_amount, bounty.reward_amount
        payment = entry(bounty, claim, kind, recorded, memo, mode, "pending")
        session.add(payment)
        return {
            "entry_id": payment.id,
            "receiver": claim.hunter_account,
            "amount": amount,
            "memo": memo,
        }


def locked_payment(session, entry_id):
    bounty_id, claim_id = session.execute(
        select(LedgerEntry.bounty_id, LedgerEntry.claim_id).where(LedgerEntry.id == entry_id)
    ).one()
    bounty = locked_bounty(session, bounty_id)
    claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
    payment = session.scalar(
        select(LedgerEntry).where(LedgerEntry.id == entry_id).with_for_update()
    )
    return bounty, claim, payment


def record_payment(entry_id, tx_id):
    """Save a sent payment. No lease check: a sent transfer is a fact to record."""
    with Session.begin() as session:
        bounty, claim, payment = locked_payment(session, entry_id)
        now = utcnow()
        payment.tx_id, payment.status, payment.updated_at = tx_id, "completed", now
        if payment.kind == "reward_out":
            session.add(
                entry(
                    bounty,
                    claim,
                    "bond_refund",
                    bounty.bond_amount,
                    payment.memo,
                    payment.settlement_mode,
                    "completed",
                    tx_id,
                )
            )
            claim.status, claim.settlement_note = "paid", "Reward and bond paid in one transfer."
        else:
            claim.status, claim.settlement_note = claim.outcome, "Bond refunded."
        claim.settled_at = claim.updated_at = now


def record_failure(entry_id):
    with Session.begin() as session:
        _, claim, payment = locked_payment(session, entry_id)
        payment.status, payment.updated_at = "failed", utcnow()
        review(claim, "The settlement reported a payment failure. No automatic retry is made.")


def settle_claim(job_id, generation, result):
    port = settlement.get_settlement()
    decide(job_id, generation, result, port.mode)
    payment = reserve_payment(job_id, generation, port.mode)
    if payment:
        try:
            tx_id = port.pay(payment["receiver"], payment["amount"], payment["memo"])
        except SettlementError:
            logger.warning("Settlement payment failed for job %s", job_id)
            record_failure(payment["entry_id"])
        else:
            record_payment(payment["entry_id"], tx_id)
    with Session() as session:
        claim = session.scalar(select(Claim).where(Claim.job_id == job_id))
        return {
            "claim_id": claim.id,
            "bounty_id": claim.bounty_id,
            "status": claim.status,
            "outcome": claim.outcome,
            "checker_reason": claim.checker_reason,
            "settlement_mode": port.mode,
            "findings": [result],
            "observations": result.get("evidence", []),
        }
