from typing import Protocol


class SettlementError(Exception):
    pass


class Settlement(Protocol):
    mode: str

    def collect(self, signed_intent: dict, *, sender: str, amount: int, memo: str) -> str: ...

    def pay(self, receiver: str, amount: int, memo: str) -> str: ...
