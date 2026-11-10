"""Settlement without network access, so dry results are never real payments.

Incoming transfers still need a valid NEP-413 or ERC-191 signature for the exact receiver,
token, amount and memo, so a dry run shows the same signing work as a real one.
"""

from uuid import uuid4

from app.core.config import Settings, settings
from app.settlement.base import SettlementError
from app.settlement.near_intents import check_amount, verify_signed_transfer

DRY_ACCOUNT = "arsune.dry"
# The Verifier salt is not checked in dry mode; clients get this value from GET /api/settlement.
DRY_SALT = "00000000"


class DrySettlement:
    mode = "dry"

    def __init__(self, config: Settings | None = None):
        config = config or settings
        self.account_id = config.near_account_id or DRY_ACCOUNT
        self.token = config.bounty_token
        self.contract = config.intents_contract

    def collect(self, signed_intent: dict, *, sender: str, amount: int, memo: str) -> str:
        verify_signed_transfer(
            signed_intent,
            sender=sender,
            receiver=self.account_id,
            token_id=self.token,
            amount=amount,
            memo=memo,
            contract=self.contract,
        )
        return f"dry-{uuid4().hex}"

    def pay(self, receiver: str, amount: int, memo: str) -> str:
        check_amount(amount)
        if not receiver or not memo:
            raise SettlementError("A receiver and memo are required.")
        if receiver == self.account_id:
            raise SettlementError("cannot pay Arsune's own account")
        return f"dry-{uuid4().hex}"
