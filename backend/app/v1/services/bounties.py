"""Owner-funded bounties: owner proof, funding, hunter claims, and read views."""

import secrets
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select

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
from app.browser.policy import TargetPolicyError, origin
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Claim, Job, LedgerEntry, PendingAction, utcnow
from app.core.schemas.bounties import CreateBounty, CreateClaim, FundBounty
from app.settlement.base import SettlementError
from app.v1.services.jobs import JOB_ID


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


def require_account(value):
    if not ACCOUNT.fullmatch(value):
        raise HTTPException(422, "Use a valid NEAR account ID.")


def fee_amount(bounty):
    return fee_for(bounty.reward_amount, bounty.fee_bps)


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
        "settlement_mode": entry.settlement_mode,
        "status": entry.status,
        "created_at": entry.created_at,
    }


def claim_summary(claim):
    return {
        "id": claim.id,
        "bounty_id": claim.bounty_id,
        "hunter_account": claim.hunter_account,
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


def ledger_rows(session, bounty_id, claim_id=None):
    query = select(LedgerEntry).where(LedgerEntry.bounty_id == bounty_id)
    if claim_id:
        query = query.where(LedgerEntry.claim_id == claim_id)
    return session.scalars(query.order_by(LedgerEntry.created_at, LedgerEntry.id)).all()


def bounty_detail(session, bounty):
    mode = settlement.get_settlement().mode
    claims = session.scalars(
        select(Claim).where(Claim.bounty_id == bounty.id).order_by(Claim.created_at, Claim.id)
    ).all()
    return {
        **bounty_summary(bounty, mode),
        "proof_token": bounty.proof_token,
        "proof_url": proof_url(bounty.url),
        "claims": [claim_summary(c) for c in claims],
        "ledger": [ledger_view(e) for e in ledger_rows(session, bounty.id)],
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
        "settlement_mode": settlement.get_settlement().mode,
    }


async def create_bounty(request: CreateBounty):
    require_account(request.owner_account)
    try:
        url = await canonical_url(request.url)
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.budget < request.reward_amount + fee_for(request.reward_amount, request.fee_bps):
        raise HTTPException(422, "The budget must cover at least one reward and its fee.")
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
    mode = settlement.get_settlement().mode
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


async def fund_bounty(bounty_id, request: FundBounty):
    require_account(request.sender)
    port = settlement.get_settlement()
    entry_id = str(uuid4())
    memo = f"arsune:fund:{entry_id}"
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
                settlement_mode=port.mode,
                status="pending",
            )
        )
    try:
        tx_id = await run_in_threadpool(
            port.collect,
            request.signed_intent,
            sender=request.sender,
            amount=request.amount,
            memo=memo,
        )
    except SettlementError:
        with Session.begin() as session:
            session.get(LedgerEntry, entry_id).status = "failed"
        raise HTTPException(402, "The funding transfer failed.") from None
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        entry = session.get(LedgerEntry, entry_id)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
        bounty.budget_amount += request.amount
        bounty.updated_at = utcnow()
        return bounty_detail(session, bounty)


async def create_claim(bounty_id, request: CreateClaim):
    require_account(request.hunter_account)
    port = settlement.get_settlement()
    with Session() as session:
        site_origin = find_bounty(session, bounty_id).origin
    try:
        urls, details = await scoped_claim(
            site_origin, request.claim_type, request.urls, request.details
        )
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    claim_id, entry_id = str(uuid4()), str(uuid4())
    memo = f"arsune:bond:{claim_id}"
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
        session.flush()
        session.add(
            LedgerEntry(
                id=entry_id,
                bounty_id=bounty.id,
                claim_id=claim_id,
                kind="bond_in",
                amount=bounty.bond_amount,
                account=request.hunter_account,
                memo=memo,
                settlement_mode=port.mode,
                status="pending",
            )
        )
        bond, site_url = bounty.bond_amount, bounty.url
    try:
        tx_id = await run_in_threadpool(
            port.collect, request.bond_intent, sender=request.hunter_account, amount=bond, memo=memo
        )
    except SettlementError:
        # The transfer did not happen, so the claim and its pending row are withdrawn.
        with Session.begin() as session:
            session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry_id))
            session.execute(delete(Claim).where(Claim.id == claim_id))
        raise HTTPException(402, "The bond transfer failed.") from None
    job_id = str(uuid4())
    with Session.begin() as session:
        entry = session.get(LedgerEntry, entry_id)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
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
        claim = session.get(Claim, claim_id)
        claim.job_id, claim.updated_at = job_id, utcnow()
        session.add(PendingAction(id=f"{job_id}:scan", job_id=job_id))
        return claim_detail(session, claim)
