from fastapi import APIRouter, Header
from fastapi.responses import Response

from app.core.schemas.bounties import CreateBounty, CreateClaim, FundBounty, SetAccessTest
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


@router.get("/bounties/{bounty_id}/access-test")
def get_access_test(bounty_id: str, x_owner_key: str | None = Header(None)):
    """Owner only: test account A and the secret markers to plant."""
    return bounties.get_access_test(bounty_id, x_owner_key)


@router.put("/bounties/{bounty_id}/access-test")
def set_access_test(bounty_id: str, request: SetAccessTest, x_owner_key: str | None = Header(None)):
    return bounties.set_access_test(bounty_id, x_owner_key, request)


def accepted_while_checking(response: Response, site_check: dict | None):
    # 202: the site check runs in the worker; read the bounty or the check again for its result.
    if site_check and site_check["state"] == "checking":
        response.status_code = 202


@router.post("/bounties/{bounty_id}/verify")
async def verify_bounty(bounty_id: str, response: Response):
    bounty = await bounties.verify_bounty(bounty_id)
    if bounty["status"] != "open":
        accepted_while_checking(response, bounty["site_check"])
    return bounty


@router.post("/bounties/{bounty_id}/site-check")
def request_site_check(bounty_id: str, response: Response):
    detail = bounties.request_site_check(bounty_id)
    accepted_while_checking(response, detail["site_check"])
    return detail


@router.get("/bounties/{bounty_id}/site-check")
def get_site_check(bounty_id: str):
    return bounties.get_site_check(bounty_id)


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
