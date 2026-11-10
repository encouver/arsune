from typing import Annotated

from pydantic import BaseModel, Field

from app.core.schemas.bounties import Amount

# 1Click checks the address format of each chain; Arsune only keeps out spaces and controls.
PlainText = Annotated[str, Field(min_length=2, max_length=200, pattern=r"^[^\s\x00-\x1f\x7f]+$")]


class CreateFundingAddress(BaseModel):
    # Bounty-token units (USDC has 6 decimals) that Arsune must receive.
    amount: Amount
    origin_asset: PlainText
    refund_to: PlainText


class SimulatedFunding(BaseModel):
    amount: Amount
