import json
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

# One billion USDC in 6-decimal units; also below the exact-integer limit of JavaScript numbers.
MAX_AMOUNT = 10**15
Amount = Annotated[int, Field(strict=True, gt=0, le=MAX_AMOUNT)]
Account = Annotated[str, Field(min_length=2, max_length=64)]


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
    sender: Account
    amount: Amount
    signed_intent: dict[str, Any]

    _intent = field_validator("signed_intent")(bounded_intent)


class CreateClaim(BaseModel):
    hunter_account: Account
    claim_type: str = Field(min_length=1, max_length=30)
    urls: list[str] = Field(min_length=1, max_length=5)
    details: dict[str, Any] = Field(max_length=5)
    explanation: str = Field(min_length=1, max_length=2000)
    reproduction_steps: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=12
    )
    bond_intent: dict[str, Any]

    _intent = field_validator("bond_intent")(bounded_intent)
