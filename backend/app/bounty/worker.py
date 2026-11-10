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

from app.bounty import budget, evidence, settlement
from app.bounty.claims import fee_for, final_fingerprint
from app.browser.checker import check_candidate
from app.browser.observations import observe_page
from app.browser.session import inspection_browser
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Claim, LedgerEntry, utcnow
from app.settlement.base import SettlementError
from app.settlement.near_tx import OutcomeUnknown
from app.workers import store

logger = logging.getLogger(__name__)

CHECK_SECONDS = 120
OUTCOMES = {
    "confirmed": "proven",
    "rejected": "rejected",
    "unverified": "unverified",
    "duplicate": "duplicate",
}
UNKNOWN_PAYMENT = (
    "A payment attempt has no recorded result. Check the settlement account before any retry."
)
# The check result of a claim whose job stopped before a decision. A timeout proves nothing.
STOPPED = {
    "status": "unverified",
    "checker_reason": "The check job reached its time or attempt limit before a decision. "
    "A timeout proves nothing, so the claim is unverified.",
    "evidence": [],
}


def locked_claim(session, job_id):
    return session.scalar(select(Claim).where(Claim.job_id == job_id).with_for_update())


def locked_bounty(session, bounty_id):
    return session.scalar(select(Bounty).where(Bounty.id == bounty_id).with_for_update())


def require_turn(session, job_id, generation):
    """Only the worker that holds the job lease may settle, or recovery after the job failed.

    Recovery passes no generation. A failed job has no worker left that could write over it.
    """
    job = store.locked_job(session, job_id)
    if generation is None and job.status == "failed":
        return
    store.require_claim(job, generation)


def lock_bounty_then_claim(session, job_id):
    """Lock in one order everywhere: bounty, then claim, then ledger row.

    A claim that becomes proven can wait for another claim of the same bounty through the
    unique index, so taking the bounty first prevents a lock cycle.
    """
    bounty_id = session.scalar(select(Claim.bounty_id).where(Claim.job_id == job_id))
    bounty = locked_bounty(session, bounty_id)
    return bounty, locked_claim(session, job_id)


def proven_holder(session, claim, fingerprint=None):
    """The claim that holds this fault. The submitted fingerprint matches a final one exactly."""
    return session.scalar(
        select(Claim.id).where(
            Claim.bounty_id == claim.bounty_id,
            Claim.final_fingerprint == (fingerprint or claim.fingerprint),
            Claim.outcome == "proven",
            Claim.id != claim.id,
        )
    )


def known_duplicate(holder_id):
    return {
        "status": "duplicate",
        "checker_reason": f"Claim {holder_id} already proved this fault. "
        "No browser check was needed.",
        "evidence": [],
    }


def start_check(job_id, generation):
    """Return the claim to check and, when the fault is already proven, its duplicate result."""
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
        known = None
        if claim.outcome == "duplicate":
            known = {"status": "duplicate", "checker_reason": claim.checker_reason, "evidence": []}
        elif holder := proven_holder(session, claim):
            known = known_duplicate(holder)
        return item, session.get(Bounty, claim.bounty_id).url, known


async def check_claim(job_id, generation, checkpoint, save_checkpoint, output_dir):
    if "result" in checkpoint:
        return checkpoint["result"]
    item, site_url, known = start_check(job_id, generation)
    if known:
        result = {**item, **known}
    else:
        result = await browser_check(job_id, item, site_url, output_dir)
    save_checkpoint({"result": result})
    return result


async def browser_check(job_id, item, site_url, output_dir):
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
        return result
    except store.LeaseLost:
        raise
    except Exception:
        # A check that cannot finish proves nothing; the bond is refunded as unverified.
        logger.exception("Claim check for job %s did not complete", job_id)
        return {
            **item,
            "status": "unverified",
            "checker_reason": "The fresh check could not complete.",
            "evidence": [],
        }


def evidence_summary(result):
    keys = ("url", "final_url", "viewport", "status", "captured_at", "screenshot")
    return [
        {key: o.get(key) for key in keys} | {"layout_complete": o.get("layout_complete", True)}
        for o in result.get("evidence", [])
    ]


def entry(bounty, claim, kind, amount, memo, mode, status, *, account, tx_id=None):
    return LedgerEntry(
        id=str(uuid4()),
        bounty_id=bounty.id,
        claim_id=claim.id,
        kind=kind,
        amount=amount,
        account=account,
        memo=memo,
        tx_id=tx_id,
        settlement_mode=mode,
        status=status,
    )


def hold_fault(session, bounty, claim, result):
    """Return (outcome, unfunded) for a claim the checker proved.

    The fault is held by its final URLs, so an alias and its final page are one fault. A
    duplicate is decided before funds are checked. A funded claim takes its reward and fee
    from the budget here, in the same transaction that holds the fault. An unfunded claim
    holds nothing, so a later funded bounty claim can prove the same fault.
    """
    final = final_fingerprint(
        claim.claim_type, claim.urls, claim.details, result.get("evidence", [])
    )
    if holder := proven_holder(session, claim, final):
        result["checker_reason"] = f"Claim {holder} already proved this fault" + (
            " at the final page." if final != claim.fingerprint else "."
        )
        return "duplicate", False
    fee = fee_for(bounty.reward_amount, bounty.fee_bps)
    if not budget.pays_one_reward(bounty):
        return "proven", True
    try:
        with session.begin_nested():
            claim.outcome, claim.final_fingerprint = "proven", final
    except IntegrityError:
        return "duplicate", False
    reserve_reward(session, bounty, claim, settlement.settlement_mode(), fee)
    return "proven", False


def charged_bond(session, claim):
    """The bond collected at intake. It depends on the hunter's reputation, not only the bounty."""
    return session.scalar(
        select(LedgerEntry.amount).where(
            LedgerEntry.claim_id == claim.id, LedgerEntry.kind == "bond_in"
        )
    )


def publish_evidence(bounty, claim, result, now):
    evidence.publish(bounty, claim, result, settings.evidence_dir / claim.job_id, now)


def forfeit_bond(session, bounty, claim, mode, now):
    """Add a rejected claim's bond to the budget. It is not a top-up: it opens nothing."""
    bond = charged_bond(session, claim)
    session.add(
        entry(
            bounty,
            claim,
            "bond_forfeit",
            bond,
            f"arsune:forfeit:{claim.id}",
            mode,
            "completed",
            account=claim.hunter_account,
        )
    )
    bounty.budget_amount += bond
    bounty.updated_at = now
    claim.settlement_note = "The bond was added to the bounty budget."
    claim.settled_at = claim.updated_at = now


def decide(job_id, generation, result, mode):
    """Save the outcome once. The partial unique index lets only one claim hold proven."""
    with Session.begin() as session:
        require_turn(session, job_id, generation)
        bounty, claim = lock_bounty_then_claim(session, job_id)
        if claim.outcome:
            if not claim.evidence_hash:
                # A duplicate known at arrival is decided without a browser check.
                publish_evidence(bounty, claim, {"status": claim.outcome}, utcnow())
            return
        outcome = OUTCOMES.get(result.get("status"), "unverified")
        unfunded = False
        if outcome == "proven":
            outcome, unfunded = hold_fault(session, bounty, claim, result)
        now = utcnow()
        claim.outcome = claim.status = outcome
        if unfunded:
            claim.status = "unfunded"
            claim.settlement_note = "The budget could not pay the reward. The bond is refunded."
            budget.close_for_budget(bounty, now)
        claim.checker_reason = result.get("checker_reason")
        claim.evidence = evidence_summary(result)
        claim.updated_at = now
        publish_evidence(bounty, claim, result, now)
        # The escrow moves the bond only when it records the ruling (rule_on_escrow).
        if outcome == "rejected" and mode != "escrow":
            forfeit_bond(session, bounty, claim, mode, now)


def scout_part(bounty, claim):
    return bounty.reward_amount * claim.scout["share_bps"] // 10_000 if claim.scout else 0


def is_funded_win(claim):
    # An unfunded claim is proven but holds no final fingerprint and gets no reward.
    return claim.outcome == "proven" and claim.final_fingerprint is not None


def payment_plan(bounty, claim, bond):
    """Transfers for a decided claim, in order: (kind, receiver, sent, recorded, memo)."""
    if claim.outcome == "rejected":
        return []
    if not is_funded_win(claim):
        return [("bond_refund", claim.hunter_account, bond, bond, f"arsune:refund:{claim.id}")]
    scout = scout_part(bounty, claim)
    hunter = bounty.reward_amount - scout
    # One transfer returns the hunter's reward part and the bond; the ledger records both.
    plan = [
        ("reward_out", claim.hunter_account, hunter + bond, hunter, f"arsune:reward:{claim.id}")
    ]
    if scout:
        plan.append(
            ("scout_share", claim.scout["account"], scout, scout, f"arsune:scout:{claim.id}")
        )
    return plan


def review(claim, note):
    claim.status, claim.settlement_note, claim.updated_at = "needs_review", note, utcnow()


def finish_claim(claim):
    if claim.outcome == "proven" and not is_funded_win(claim):
        claim.status, claim.settlement_note = "unfunded", "Bond refunded; the budget was spent."
    elif claim.outcome == "proven":
        claim.status = "paid"
        claim.settlement_note = "Reward and bond paid" + (
            "; the scout share was paid separately." if claim.scout else "."
        )
    else:
        claim.status, claim.settlement_note = claim.outcome, "Bond refunded."
    claim.settled_at = claim.updated_at = utcnow()


def claim_rows(session, claim):
    rows = session.scalars(select(LedgerEntry).where(LedgerEntry.claim_id == claim.id))
    return {row.kind: row for row in rows}


def reserve_reward(session, bounty, claim, mode, fee):
    """Take the reward and fee from the budget, once, when the claim becomes proven."""
    bounty.budget_amount -= bounty.reward_amount + fee
    bounty.updated_at = utcnow()
    if not budget.pays_one_reward(bounty):
        budget.close_for_budget(bounty, bounty.updated_at)
    if fee:
        session.add(
            entry(
                bounty,
                claim,
                "fee",
                fee,
                f"arsune:fee:{claim.id}",
                mode,
                "completed",
                account=settlement.arsune_account(),
            )
        )


def next_payment(job_id, generation, mode):
    """Commit the next payment row before its transfer; return the transfer to send, if any."""
    with Session.begin() as session:
        require_turn(session, job_id, generation)
        bounty, claim = lock_bounty_then_claim(session, job_id)
        if claim.settled_at or claim.status == "needs_review":
            return None
        rows = claim_rows(session, claim)
        plan = payment_plan(bounty, claim, rows["bond_in"].amount)
        for kind, receiver, amount, recorded, memo in plan:
            row = rows.get(kind)
            if row and row.tx_id:
                continue
            if row:
                review(claim, UNKNOWN_PAYMENT)
                return None
            payment = entry(bounty, claim, kind, recorded, memo, mode, "pending", account=receiver)
            session.add(payment)
            return {"entry_id": payment.id, "receiver": receiver, "amount": amount, "memo": memo}
        finish_claim(claim)
        return None


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
        payment.tx_id, payment.status, payment.updated_at = tx_id, "completed", utcnow()
        bond = charged_bond(session, claim)
        if payment.kind == "reward_out":
            session.add(
                entry(
                    bounty,
                    claim,
                    "bond_refund",
                    bond,
                    payment.memo,
                    payment.settlement_mode,
                    "completed",
                    account=claim.hunter_account,
                    tx_id=tx_id,
                )
            )
        rows = claim_rows(session, claim)
        plan = payment_plan(bounty, claim, bond)
        if all(rows.get(kind) and rows[kind].tx_id for kind, *_ in plan):
            finish_claim(claim)


def record_failure(entry_id, unknown_tx=None):
    with Session.begin() as session:
        _, claim, payment = locked_payment(session, entry_id)
        payment.status, payment.updated_at = ("unknown" if unknown_tx else "failed"), utcnow()
        if unknown_tx:
            review(claim, f"The transfer result is unknown (tx {unknown_tx}). {UNKNOWN_PAYMENT}")
        else:
            review(claim, "The settlement reported a payment failure. No automatic retry is made.")


def pay_claim(job_id, generation, mode):
    while payment := next_payment(job_id, generation, mode):
        try:
            tx_id = settlement.pay(payment["receiver"], payment["amount"], payment["memo"])
        except OutcomeUnknown as exc:
            logger.warning("Settlement outcome unknown for job %s", job_id)
            record_failure(payment["entry_id"], exc.tx_hash)
            return
        except SettlementError:
            logger.warning("Settlement payment failed for job %s", job_id)
            record_failure(payment["entry_id"])
            return
        if not isinstance(tx_id, str) or not tx_id:
            # A transfer without a transaction ID cannot be proven sent or unsent.
            record_failure(payment["entry_id"], "not returned")
            return
        record_payment(payment["entry_id"], tx_id)


def rule_entry(bounty, claim, bond):
    """The ledger row saved before `rule`: (kind, account, amount, memo)."""
    if claim.outcome == "rejected":
        return "bond_forfeit", claim.hunter_account, bond, f"arsune:forfeit:{claim.id}"
    kind, receiver, _, recorded, memo = payment_plan(bounty, claim, bond)[0]
    return kind, receiver, recorded, memo


def next_rule(job_id, generation, mode):
    """Commit the ruling's ledger row before the rule transaction; return the call to send."""
    with Session.begin() as session:
        require_turn(session, job_id, generation)
        bounty, claim = lock_bounty_then_claim(session, job_id)
        if claim.settled_at or claim.status == "needs_review":
            return None
        kind, account, amount, memo = rule_entry(bounty, claim, charged_bond(session, claim))
        row = claim_rows(session, claim).get(kind)
        if row:
            # A row without a transaction ID means a rule transaction may have been sent.
            if not row.tx_id:
                review(claim, UNKNOWN_PAYMENT)
            return None
        row = entry(bounty, claim, kind, amount, memo, mode, "pending", account=account)
        session.add(row)
        return {
            "entry_id": row.id,
            "claim_id": claim.id,
            "ruling": evidence.ruling(claim),
            "evidence_hash": claim.evidence_hash,
        }


def record_rule(entry_id, call):
    """Save a rule transaction and the transfers that the escrow made in it."""
    with Session.begin() as session:
        bounty, claim, row = locked_payment(session, entry_id)
        now = utcnow()
        event = next((e for e in call.events if e["event"] == "rule"), {})
        expected = (evidence.ruling(claim), claim.evidence_hash)
        if (event.get("outcome"), event.get("evidence_hash")) != expected:
            row.status, row.updated_at = "failed", now
            review(
                claim,
                f"The escrow ruled {event.get('outcome')} in tx {call.tx_hash}, not "
                f"{expected[0]} with this evidence hash. Check the escrow before any action.",
            )
            return
        row.tx_id, row.status, row.updated_at = call.tx_hash, "completed", now
        if row.kind == "bond_forfeit":
            bounty.budget_amount += row.amount
            bounty.updated_at = now
            claim.settlement_note = "The bond was added to the bounty budget."
            claim.settled_at = claim.updated_at = now
            return
        if row.kind == "reward_out":
            paid = [("bond_refund", claim.hunter_account, charged_bond(session, claim), row.memo)]
            if claim.scout:
                scout, share = claim.scout["account"], scout_part(bounty, claim)
                paid.append(("scout_share", scout, share, f"arsune:scout:{claim.id}"))
            for kind, account, amount, memo in paid:
                session.add(
                    entry(
                        bounty,
                        claim,
                        kind,
                        amount,
                        memo,
                        row.settlement_mode,
                        "completed",
                        account=account,
                        tx_id=call.tx_hash,
                    )
                )
            if fee := claim_rows(session, claim).get("fee"):
                fee.tx_id = call.tx_hash
        finish_claim(claim)


def rule_on_escrow(job_id, generation, mode):
    step = next_rule(job_id, generation, mode)
    if not step:
        return
    try:
        call = settlement.rule(step["claim_id"], step["ruling"], step["evidence_hash"])
    except OutcomeUnknown as exc:
        logger.warning("Escrow rule outcome unknown for job %s", job_id)
        record_failure(step["entry_id"], exc.tx_hash)
        return
    except SettlementError:
        logger.warning("Escrow rule failed for job %s", job_id)
        record_failure(step["entry_id"])
        return
    record_rule(step["entry_id"], call)


def settle_claim(job_id, generation, result):
    mode = settlement.settlement_mode()
    decide(job_id, generation, result, mode)
    if mode == "escrow":
        rule_on_escrow(job_id, generation, mode)
    else:
        pay_claim(job_id, generation, mode)
    with Session() as session:
        claim = session.scalar(select(Claim).where(Claim.job_id == job_id))
        return {
            "claim_id": claim.id,
            "bounty_id": claim.bounty_id,
            "status": claim.status,
            "outcome": claim.outcome,
            "checker_reason": claim.checker_reason,
            "evidence_hash": claim.evidence_hash,
            "settlement_mode": mode,
            "findings": [result],
            "observations": result.get("evidence", []),
        }


def settle_stopped_claim(job_id):
    """Settle a claim whose job failed at its time or attempt limit, without a worker lease.

    A claim without an outcome becomes unverified and gets the ordinary refund (in escrow mode
    an `unverified` ruling). A decided claim gets the rest of its settlement. A payment row
    without a transaction ID still sends the claim to review: its result is unknown.
    """
    return settle_claim(job_id, None, STOPPED)
