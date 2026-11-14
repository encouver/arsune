from fastapi import APIRouter, Header

from app.core.schemas.bounties import OwnerLogin
from app.v1.services import owner

router = APIRouter(prefix="/owner")


@router.post("/challenge")
def challenge():
    """The message, nonce and recipient the owner's wallet signs (NEP-413) to sign in."""
    return owner.challenge()


@router.post("/login")
def login(request: OwnerLogin):
    return owner.login(request)


@router.get("/bounties")
def owner_bounties(authorization: str | None = Header(None)):
    return owner.owner_bounties(owner.required_account(authorization))
