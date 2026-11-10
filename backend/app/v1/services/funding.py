"""Owner funding: options, 1Click deposit addresses, simulated funds, and the demo proof."""

from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from app.bounty import budget, crosschain, settlement
from app.bounty.proof import proof_url
from app.browser.policy import origin
from app.core.config import settings
from app.core.db import Session
from app.core.models import FundingRequest, utcnow
from app.core.schemas.bounties import FundBounty
from app.core.schemas.funding import CreateFundingAddress, SimulatedFunding
from app.settlement.near_intents import sign_transfer
from app.settlement.near_tx import generate_key, implicit_account_id, load_signing_key
from app.v1.services import bounties

MAX_OPEN_REQUESTS = 5
LIST_LIMIT = 20


def options():
    mode = settlement.settlement_mode()
    return {
        "settlement_mode": mode,
        "arsune_account": settlement.arsune_account(),
        "token": settings.bounty_token,
        "token_symbol": "USDC",
        "token_decimals": 6,
        "intents_contract": settings.intents_contract,
        "demo_site_url": settings.demo_url.rstrip("/"),
        "demo_site_origin": origin(settings.demo_url),
        "simulated_funding": mode == "dry",
        "crosschain": crosschain.availability(),
    }


def create_funding_address(bounty_id, request: CreateFundingAddress):
    if settlement.settlement_mode() != "near_intents":
        raise HTTPException(409, crosschain.DRY_REASON)
    with Session() as session:
        bounty = bounties.find_bounty(session, bounty_id)
        if bounty.status == "pending_proof":
            raise HTTPException(409, "Prove ownership of the site before you fund the bounty.")
        if not budget.takes_funds(bounty):
            raise HTTPException(409, bounties.FUNDS_RULE)
        unfinished = session.scalar(
            select(func.count())
            .select_from(FundingRequest)
            .where(
                FundingRequest.bounty_id == bounty_id,
                FundingRequest.status.not_in(crosschain.FINAL),
                FundingRequest.deadline > utcnow(),
            )
        )
    if unfinished >= MAX_OPEN_REQUESTS:
        raise HTTPException(
            429,
            f"This bounty already has {MAX_OPEN_REQUESTS} deposit addresses that wait for a "
            "deposit. Use one of them, or wait until their send-before time passes.",
        )
    try:
        token = crosschain.find_token(request.origin_asset)
        if token is None:
            raise HTTPException(422, "1Click does not support this origin asset.")
        return crosschain.create_request(
            bounty_id, token=token, amount=request.amount, refund_to=request.refund_to
        )
    except crosschain.OneClickRefused as exc:
        raise HTTPException(422, f"1Click refused the quote: {exc}") from None
    except crosschain.OneClickError as exc:
        raise HTTPException(503, f"No deposit address was made. {exc}") from None


def list_funding(bounty_id):
    with Session() as session:
        bounties.find_bounty(session, bounty_id)
    reachable = crosschain.refresh_open(bounty_id)
    with Session() as session:
        bounty = bounties.find_bounty(session, bounty_id)
        rows = session.scalars(
            select(FundingRequest)
            .where(FundingRequest.bounty_id == bounty_id)
            .order_by(FundingRequest.created_at.desc(), FundingRequest.id)
            .limit(LIST_LIMIT)
        ).all()
        return {
            "bounty_id": bounty.id,
            "bounty_status": bounty.status,
            "budget_amount": bounty.budget_amount,
            "budget_target": bounty.budget_target,
            "settlement_mode": settlement.settlement_mode(),
            "requests": [crosschain.request_view(row) for row in rows],
            "status_error": None
            if reachable
            else "1Click cannot be reached now. These are the last known statuses.",
        }


async def fund_simulated(bounty_id, request: SimulatedFunding):
    """Fund through the normal signed-transfer path with a throwaway key. Dry mode only."""
    if settlement.settlement_mode() != "dry":
        raise HTTPException(
            409,
            "Simulated funding exists only in dry settlement mode. "
            "Fund with a signed transfer or a 1Click deposit.",
        )
    private_key, _ = generate_key()
    sender = implicit_account_id(load_signing_key(private_key))
    fund_id = str(uuid4())
    memo = f"arsune:fund:{fund_id}"
    signed = sign_transfer(
        sender,
        private_key,
        settlement.arsune_account(),
        settings.bounty_token,
        request.amount,
        memo,
        utcnow() + timedelta(minutes=5),
        salt=bytes.fromhex(settlement.current_salt()),
        contract=settings.intents_contract,
    )
    detail = await bounties.fund_bounty(
        bounty_id,
        FundBounty(fund_id=fund_id, sender=sender, amount=request.amount, signed_intent=signed),
    )
    return {
        "simulated": True,
        "fund_id": fund_id,
        "sender": sender,
        "memo": memo,
        "amount": request.amount,
        "bounty": detail,
    }


async def demo_proof(bounty_id):
    """Publish the proof token on the controlled demo site; other sites must do it themselves."""
    with Session() as session:
        bounty = bounties.find_bounty(session, bounty_id)
        if bounty.origin != origin(settings.demo_url):
            raise HTTPException(
                409,
                "Only a bounty on the local demo site can set its proof automatically. "
                "Publish the token on your own site.",
            )
        if bounty.status != "pending_proof":
            raise HTTPException(409, "This bounty does not wait for proof.")
        token, url = bounty.proof_token, bounty.url
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.post(
                settings.demo_url.rstrip("/") + "/bounty-proof-control", json={"token": token}
            )
            response.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(503, "The demo site did not accept the proof token.") from None
    return {"published": True, "proof_url": proof_url(url)}
