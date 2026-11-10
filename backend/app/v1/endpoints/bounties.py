from fastapi import APIRouter
from fastapi.responses import Response

from app.core.schemas.bounties import CreateBounty, CreateClaim, FundBounty
from app.v1.services import bounties

router = APIRouter()


@router.get("/settlement")
def settlement():
    return bounties.settlement_view()


@router.post("/bounties", status_code=201)
async def create_bounty(request: CreateBounty):
    return await bounties.create_bounty(request)


@router.get("/bounties")
def list_bounties():
    return bounties.list_bounties()


@router.get("/bounties/{bounty_id}")
def get_bounty(bounty_id: str):
    return bounties.get_bounty(bounty_id)


@router.post("/bounties/{bounty_id}/verify")
async def verify_bounty(bounty_id: str):
    return await bounties.verify_bounty(bounty_id)


@router.post("/bounties/{bounty_id}/fund")
async def fund_bounty(bounty_id: str, request: FundBounty):
    return await bounties.fund_bounty(bounty_id, request)


@router.post("/bounties/{bounty_id}/claims", status_code=202)
async def create_claim(bounty_id: str, request: CreateClaim):
    return await bounties.create_claim(bounty_id, request)


@router.get("/bounties/{bounty_id}/claims/{claim_id}")
def get_claim(bounty_id: str, claim_id: str):
    return bounties.get_claim(bounty_id, claim_id)


@router.post("/bounties/{bounty_id}/withdraw")
def withdraw_bounty(bounty_id: str):
    return bounties.withdraw_bounty(bounty_id)


@router.get("/claims/{claim_id}")
def get_claim_by_id(claim_id: str):
    return bounties.get_claim_by_id(claim_id)


@router.get("/claims/{claim_id}/evidence")
def claim_evidence(claim_id: str):
    # The stored bytes, unchanged: their sha256 is the claim's evidence_hash.
    data, digest = bounties.claim_evidence(claim_id)
    return Response(data, media_type="application/json", headers={"X-Evidence-Hash": digest})


@router.get("/bounties/{bounty_id}/bond")
def get_bond(bounty_id: str, hunter: str):
    return bounties.get_bond(bounty_id, hunter)


@router.get("/hunters/{account}")
def get_hunter(account: str):
    return bounties.get_hunter(account)
