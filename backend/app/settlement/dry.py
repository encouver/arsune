"""Settlement without network access, so dry results are never real payments."""

from uuid import uuid4

from app.settlement.base import SettlementError


class DrySettlement:
    mode = "dry"

    def collect(self, signed_intent: dict, *, sender: str, amount: int, memo: str) -> str:
        if not isinstance(signed_intent, dict) or not signed_intent:
            raise SettlementError("A signed transfer intent is required.")
        _check(sender, amount, memo)
        return f"dry-{uuid4().hex}"

    def pay(self, receiver: str, amount: int, memo: str) -> str:
        _check(receiver, amount, memo)
        return f"dry-{uuid4().hex}"


def _check(account: str, amount: int, memo: str):
    if not account or not memo:
        raise SettlementError("An account and memo are required.")
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise SettlementError("The amount must be a positive integer.")
