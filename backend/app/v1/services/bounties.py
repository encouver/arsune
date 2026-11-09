"""Owner-funded bounties: owner proof, funding, hunter claims, and read views."""

import secrets
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from app.bounty import settlement
from app.bounty.claims import (
    ACCOUNT,
    MAX_UNRESOLVED_CLAIMS,
    ClaimError,
    canonical_url,
    fee_for,
    fingerprint,
    scoped_claim,
)
from app.bounty.proof import ProofError, proof_matches, proof_url
from app.bounty.recovery import queue_check
from app.browser.policy import TargetPolicyError, origin
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Claim, Job, LedgerEntry, utcnow
from app.core.schemas.bounties import CreateBounty, CreateClaim, FundBounty
from app.settlement.base import SettlementError
from app.settlement.near_tx import OutcomeUnknown
from app.v1.services.jobs import JOB_ID

OUTCOME_COUNTS = ("proven", "unfunded", "rejected", "duplicate", "unverified")


def find_bounty(session, bounty_id, lock=False):
    # Bounty IDs use the same canonical UUID form as jobs; other text never reaches the database.
    query = select(Bounty).where(Bounty.id == bounty_id)
    bounty = (
        session.scalar(query.with_for_update() if lock else query)
        if JOB_ID.fullmatch(bounty_id)
        else None
    )
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    return bounty


def find_claim(session, bounty_id, claim_id):
    claim = session.get(Claim, claim_id) if JOB_ID.fullmatch(claim_id) else None
    if not claim or claim.bounty_id != bounty_id:
        raise HTTPException(404, "Claim not found")
    return claim


def require_account(value, role="account"):
    if not ACCOUNT.fullmatch(value):
        raise HTTPException(422, f"Use a valid NEAR account ID for the {role}.")
    if value == settlement.arsune_account():
        raise HTTPException(422, f"The {role} cannot be Arsune's settlement account.")


def check_intent(signed, *, sender, amount, memo):
    try:
        settlement.check_transfer(signed, sender=sender, amount=amount, memo=memo)
    except SettlementError as exc:
        raise HTTPException(422, f"The signed transfer is not valid: {exc}") from None


def fee_amount(bounty):
    return fee_for(bounty.reward_amount, bounty.fee_bps)


def settlement_view():
    try:
        return settlement.settlement_info()
    except SettlementError:
        raise HTTPException(503, "The settlement network is unavailable.") from None


def bounty_summary(bounty, mode):
    return {
        "id": bounty.id,
        "url": bounty.url,
        "origin": bounty.origin,
        "owner_account": bounty.owner_account,
        "status": bounty.status,
        "reward_amount": bounty.reward_amount,
        "bond_amount": bounty.bond_amount,
        "fee_bps": bounty.fee_bps,
        "fee_amount": fee_amount(bounty),
        "budget_amount": bounty.budget_amount,
        "budget_target": bounty.budget_target,
        "settlement_mode": mode,
        "created_at": bounty.created_at,
        "updated_at": bounty.updated_at,
    }


def ledger_view(entry):
    return {
        "id": entry.id,
        "claim_id": entry.claim_id,
        "kind": entry.kind,
        "amount": entry.amount,
        "account": entry.account,
        "memo": entry.memo,
        "tx_id": entry.tx_id,
        "explorer_url": settlement.explorer_url(entry.settlement_mode, entry.tx_id),
        "settlement_mode": entry.settlement_mode,
        "status": entry.status,
        "created_at": entry.created_at,
    }


def claim_summary(claim):
    return {
        "id": claim.id,
        "claim_id": claim.id,
        "bounty_id": claim.bounty_id,
        "hunter_account": claim.hunter_account,
        "scout": claim.scout,
        "claim_type": claim.claim_type,
        "urls": claim.urls,
        "details": claim.details,
        "fingerprint": claim.fingerprint,
        "status": claim.status,
        "outcome": claim.outcome,
        "checker_reason": claim.checker_reason,
        "settlement_note": claim.settlement_note,
        "job_id": claim.job_id,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
        "settled_at": claim.settled_at,
    }


def hunter_summary(claims, ledger):
    """One row for every hunter and scout account seen in the bounty."""
    people = {}

    def person(account):
        return people.setdefault(
            account,
            {"account": account, "claims": 0, "pending": 0}
            | dict.fromkeys(OUTCOME_COUNTS, 0)
            | {"earned": 0, "bonds_lost": 0, "scout_earned": 0},
        )

    for claim in claims:
        row = person(claim.hunter_account)
        row["claims"] += 1
        row[claim.status if claim.status == "unfunded" else claim.outcome or "pending"] += 1
        if claim.scout:
            person(claim.scout["account"])
    for entry in ledger:
        if entry.kind == "reward_out" and entry.tx_id:
            person(entry.account)["earned"] += entry.amount
        elif entry.kind == "scout_share" and entry.tx_id:
            person(entry.account)["scout_earned"] += entry.amount
        elif entry.kind == "bond_forfeit":
            person(entry.account)["bonds_lost"] += entry.amount
    return sorted(people.values(), key=lambda p: (-p["earned"] - p["scout_earned"], p["account"]))


def ledger_rows(session, bounty_id, claim_id=None):
    query = select(LedgerEntry).where(LedgerEntry.bounty_id == bounty_id)
    if claim_id:
        query = query.where(LedgerEntry.claim_id == claim_id)
    return session.scalars(query.order_by(LedgerEntry.created_at, LedgerEntry.id)).all()


def bounty_detail(session, bounty):
    claims = session.scalars(
        select(Claim).where(Claim.bounty_id == bounty.id).order_by(Claim.created_at, Claim.id)
    ).all()
    ledger = ledger_rows(session, bounty.id)
    return {
        **bounty_summary(bounty, settlement.settlement_mode()),
        "proof_token": bounty.proof_token,
        "proof_url": proof_url(bounty.url),
        "claims": [claim_summary(c) for c in claims],
        "ledger": [ledger_view(e) for e in ledger],
        "hunters": hunter_summary(claims, ledger),
    }


def claim_detail(session, claim):
    job = session.get(Job, claim.job_id) if claim.job_id else None
    evidence = [
        {
            **item,
            "screenshot_url": f"/api/jobs/{claim.job_id}/evidence/{item['screenshot']}"
            if item.get("screenshot")
            else None,
        }
        for item in claim.evidence
    ]
    return {
        **claim_summary(claim),
        "explanation": claim.explanation,
        "reproduction_steps": claim.reproduction_steps,
        "evidence": evidence,
        "job_status": job.status if job else None,
        "ledger": [ledger_view(e) for e in ledger_rows(session, claim.bounty_id, claim.id)],
        "settlement_mode": settlement.settlement_mode(),
    }


async def create_bounty(request: CreateBounty):
    require_account(request.owner_account, "owner")
    try:
        url = await canonical_url(request.url)
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.budget < request.reward_amount + fee_for(request.reward_amount, request.fee_bps):
        raise HTTPException(422, "The budget must cover at least one reward and its fee.")
    if (
        settings.settlement_mode == "near_intents"
        and request.reward_amount + request.bond_amount > settings.max_payment_amount
    ):
        raise HTTPException(422, "The reward and bond exceed the largest permitted payment.")
    with Session.begin() as session:
        bounty = Bounty(
            id=str(uuid4()),
            url=url,
            origin=origin(url),
            owner_account=request.owner_account,
            proof_token=secrets.token_urlsafe(24),
            status="pending_proof",
            reward_amount=request.reward_amount,
            bond_amount=request.bond_amount,
            fee_bps=request.fee_bps,
            budget_amount=0,
            budget_target=request.budget,
        )
        session.add(bounty)
        session.flush()
        return bounty_detail(session, bounty)


def list_bounties(limit=50):
    mode = settlement.settlement_mode()
    with Session() as session:
        rows = session.scalars(
            select(Bounty).order_by(Bounty.created_at.desc(), Bounty.id.desc()).limit(limit)
        ).all()
        return {"items": [bounty_summary(b, mode) for b in rows], "settlement_mode": mode}


def get_bounty(bounty_id):
    with Session() as session:
        return bounty_detail(session, find_bounty(session, bounty_id))


def get_claim(bounty_id, claim_id):
    with Session() as session:
        find_bounty(session, bounty_id)
        return claim_detail(session, find_claim(session, bounty_id, claim_id))


async def verify_bounty(bounty_id):
    with Session() as session:
        bounty = find_bounty(session, bounty_id)
        if bounty.status == "open":
            return bounty_detail(session, bounty)
        if bounty.status != "pending_proof":
            raise HTTPException(409, "Only a bounty that waits for proof can be verified.")
        url, token = bounty.url, bounty.proof_token
    try:
        matches = await proof_matches(url, token)
    except ProofError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not matches:
        raise HTTPException(409, "The proof file does not contain this bounty's token.")
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status == "pending_proof":
            bounty.status, bounty.updated_at = "open", utcnow()
        return bounty_detail(session, bounty)


async def transfer_in(signed, *, sender, amount, memo):
    """Collect a signed transfer. Return the transaction ID, or the unknown-result error."""
    return await run_in_threadpool(
        settlement.collect, signed, sender=sender, amount=amount, memo=memo
    )


async def fund_bounty(bounty_id, request: FundBounty):
    require_account(request.sender, "sender")
    entry_id, memo = request.fund_id, f"arsune:fund:{request.fund_id}"
    with Session() as session:
        find_bounty(session, bounty_id)
        if session.get(LedgerEntry, entry_id):
            raise HTTPException(409, "This fund_id is already used.")
    check_intent(request.signed_intent, sender=request.sender, amount=request.amount, memo=memo)
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status != "open":
            raise HTTPException(409, "Only an open bounty can receive funds.")
        # The row exists before the transfer, so an interrupted request is visible as pending.
        session.add(
            LedgerEntry(
                id=entry_id,
                bounty_id=bounty.id,
                kind="fund",
                amount=request.amount,
                account=request.sender,
                memo=memo,
                settlement_mode=settlement.settlement_mode(),
                status="pending",
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "This fund_id is already used.") from None
    try:
        tx_id = await transfer_in(
            request.signed_intent, sender=request.sender, amount=request.amount, memo=memo
        )
    except SettlementError as exc:
        unknown = isinstance(exc, OutcomeUnknown)
        with Session.begin() as session:
            session.get(LedgerEntry, entry_id).status = "unknown" if unknown else "failed"
        if unknown:
            raise HTTPException(504, "The funding transfer result is unknown.") from None
        raise HTTPException(402, "The funding transfer failed.") from None
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        entry = session.get(LedgerEntry, entry_id)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
        bounty.budget_amount += request.amount
        bounty.updated_at = utcnow()
        return bounty_detail(session, bounty)


async def create_claim(bounty_id, request: CreateClaim):
    require_account(request.hunter_account, "hunter")
    if request.scout:
        require_account(request.scout.account, "scout")
    claim_id, memo = request.claim_id, f"arsune:bond:{request.claim_id}"
    with Session() as session:
        bounty = find_bounty(session, bounty_id)
        if session.get(Claim, claim_id):
            raise HTTPException(409, "This claim_id is already used.")
        if bounty.status == "closed":
            raise HTTPException(409, "The bounty is closed.")
        site_origin, bond = bounty.origin, bounty.bond_amount
    try:
        urls, details = await scoped_claim(
            site_origin, request.claim_type, request.urls, request.details
        )
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    check_intent(request.bond_intent, sender=request.hunter_account, amount=bond, memo=memo)
    entry_id = str(uuid4())
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status != "open":
            raise HTTPException(409, "The bounty is not open.")
        if bounty.budget_amount < bounty.reward_amount + fee_amount(bounty):
            raise HTTPException(409, "The bounty budget cannot pay another reward.")
        unresolved = session.scalar(
            select(func.count())
            .select_from(Claim)
            .where(
                Claim.bounty_id == bounty.id,
                Claim.hunter_account == request.hunter_account,
                Claim.settled_at.is_(None),
            )
        )
        if unresolved >= MAX_UNRESOLVED_CLAIMS:
            raise HTTPException(429, "This hunter has too many unresolved claims on this bounty.")
        session.add(
            Claim(
                id=claim_id,
                bounty_id=bounty.id,
                hunter_account=request.hunter_account,
                scout=request.scout.model_dump() if request.scout else None,
                claim_type=request.claim_type,
                urls=urls,
                details=details,
                explanation=request.explanation,
                reproduction_steps=request.reproduction_steps,
                fingerprint=fingerprint(request.claim_type, urls, details),
                status="received",
                evidence=[],
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "This claim_id is already used.") from None
        session.add(
            LedgerEntry(
                id=entry_id,
                bounty_id=bounty.id,
                claim_id=claim_id,
                kind="bond_in",
                amount=bond,
                account=request.hunter_account,
                memo=memo,
                settlement_mode=settlement.settlement_mode(),
                status="pending",
            )
        )
        site_url = bounty.url
    try:
        tx_id = await transfer_in(
            request.bond_intent, sender=request.hunter_account, amount=bond, memo=memo
        )
    except OutcomeUnknown as exc:
        with Session.begin() as session:
            claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
            session.get(LedgerEntry, entry_id).status = "unknown"
            claim.status, claim.updated_at = "needs_review", utcnow()
            claim.settlement_note = f"The bond transfer result is unknown (tx {exc.tx_hash})."
        raise HTTPException(504, "The bond transfer result is unknown.") from None
    except SettlementError:
        # The transfer did not happen, so the claim and its pending row are withdrawn.
        with Session.begin() as session:
            session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry_id))
            session.execute(delete(Claim).where(Claim.id == claim_id))
        raise HTTPException(402, "The bond transfer failed.") from None
    record_bond(claim_id, entry_id, tx_id)
    return start_claim(claim_id, site_url)


def record_bond(claim_id, entry_id, tx_id):
    # Saved alone first, so recovery can queue the check if the next step does not run.
    with Session.begin() as session:
        claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
        entry = session.get(LedgerEntry, entry_id, with_for_update=True)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
        if claim.status == "needs_review" and not claim.job_id and not claim.settled_at:
            # Intake recovery held this claim while the transfer was slow; the result is known now.
            claim.status, claim.settlement_note, claim.updated_at = "received", None, utcnow()


def start_claim(claim_id, site_url):
    with Session.begin() as session:
        claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
        queue_check(session, claim, site_url)
        return claim_detail(session, claim)
