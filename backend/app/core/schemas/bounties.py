import json
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

# One billion USDC in 6-decimal units; also below the exact-integer limit of JavaScript numbers.
MAX_AMOUNT = 10**15
Amount = Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT)]
Account = Annotated[str, Field(min_length=2, max_length=64)]
# Clients make these IDs so they can sign the memo before the request.
ClientId = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
]


def bounded_intent(value: dict[str, Any]) -> dict[str, Any]:
    if not value or len(json.dumps(value)) > 16_384:
        raise ValueError("Supply a non-empty signed intent of at most 16 KB.")
    return value


class CreateBounty(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    owner_account: Account
    reward_amount: Amount
    bond_amount: Amount
    fee_bps: int = Field(1000, strict=True, ge=0, le=10_000)
    budget: Amount


class FundBounty(BaseModel):
    fund_id: ClientId
    sender: Account
    amount: Amount
    signed_intent: dict[str, Any]

    _intent = field_validator("signed_intent")(bounded_intent)


class Scout(BaseModel):
    account: Account
    share_bps: int = Field(strict=True, ge=1, le=5000)
    tip_id: str = Field(min_length=1, max_length=64, pattern=r"^[^\x00-\x1f\x7f]+$")


class CreateClaim(BaseModel):
    claim_id: ClientId
    hunter_account: Account
    scout: Scout | None = None
    claim_type: str = Field(min_length=1, max_length=30)
    urls: list[str] = Field(min_length=1, max_length=5)
    details: dict[str, Any] = Field(max_length=5)
    explanation: str = Field(min_length=1, max_length=2000)
    reproduction_steps: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=12
    )
    bond_intent: dict[str, Any]

    _intent = field_validator("bond_intent")(bounded_intent)


class SetAccessTest(BaseModel):
    """Test account A: Arsune logs in on this page in a real browser before a claim's steps."""

    login_url: str = Field(min_length=8, max_length=2048)
    email: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=256)
