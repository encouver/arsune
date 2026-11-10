from fastapi import APIRouter

from app.core.schemas.funding import CreateFundingAddress, SimulatedFunding
from app.v1.services import funding

router = APIRouter()


@router.get("/funding/options")
def funding_options():
    return funding.options()


@router.post("/bounties/{bounty_id}/funding-address", status_code=201)
def create_funding_address(bounty_id: str, request: CreateFundingAddress):
    return funding.create_funding_address(bounty_id, request)


@router.get("/bounties/{bounty_id}/funding")
def list_funding(bounty_id: str):
    return funding.list_funding(bounty_id)


@router.post("/bounties/{bounty_id}/fund-simulated")
async def fund_simulated(bounty_id: str, request: SimulatedFunding):
    return await funding.fund_simulated(bounty_id, request)


@router.post("/bounties/{bounty_id}/demo-proof")
async def demo_proof(bounty_id: str):
    return await funding.demo_proof(bounty_id)
