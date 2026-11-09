"""An agent's money and track record.

In simulation the wallet is the agent's own bookkeeping of Arsune's public ledger rows for its
account. In live mode the balance is the agent's real USDC balance in intents.near.
"""

from collections import Counter
from dataclasses import dataclass, field

SPENT = {"bond_in", "fund"}  # transfers from this account to Arsune
RECEIVED = {"reward_out", "bond_refund", "scout_share"}  # transfers from Arsune
EARNED = {"reward_out", "scout_share"}


@dataclass
class Wallet:
    account: str
    start: int = 0
    live: bool = False
    live_balance: int | None = None
    spent: int = 0
    received: int = 0
    earned: int = 0
    bonds_lost: int = 0
    reserved: int = 0
    _seen: set = field(default_factory=set, repr=False)

    def apply(self, rows: list[dict]) -> list[dict]:
        """Book completed ledger rows for this account once each; return the new ones."""
        new = []
        for row in rows:
            if (
                row.get("account") != self.account
                or row.get("status") != "completed"
                or row.get("id") in self._seen
            ):
                continue
            self._seen.add(row.get("id"))
            amount, kind = int(row["amount"]), row["kind"]
            if kind in SPENT:
                self.spent += amount
            elif kind in RECEIVED:
                self.received += amount
                if kind in EARNED:
                    self.earned += amount
            elif kind == "bond_forfeit":
                self.bonds_lost += amount
            new.append(row)
        return new

    @property
    def balance(self) -> int:
        if self.live:
            return self.live_balance or 0
        return self.start + self.received - self.spent

    @property
    def available(self) -> int:
        return self.balance - self.reserved

    def can_afford(self, amount: int) -> bool:
        return self.available >= amount


def is_broke(wallet: Wallet, bond: int, pending_claims: int) -> bool:
    """Broke means no bond money and no unresolved claim that could refund one."""
    return not wallet.can_afford(bond) and pending_claims == 0


@dataclass
class Record:
    """Resolved claim outcomes per claim type."""

    outcomes: dict[str, Counter] = field(default_factory=dict)

    def add(self, claim_type: str, outcome: str):
        self.outcomes.setdefault(claim_type, Counter())[outcome] += 1

    def proven(self, claim_type: str) -> int:
        return self.outcomes.get(claim_type, Counter())["proven"]

    def resolved(self, claim_type: str) -> int:
        return sum(self.outcomes.get(claim_type, Counter()).values())
