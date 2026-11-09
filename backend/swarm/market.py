"""The tip board: scouts sell findings to hunters for a share of the reward.

A tip shows its claim type and asking share; the pages are revealed only to the buyer. A
hunter values a tip from its own track record, then accepts the ask, counters once, or walks
away. The scout accepts or refuses a counter. Arsune pays the scout its share only when the
buyer's claim is proven, so a scout gains nothing from a false tip.
"""

from dataclasses import dataclass, field
from uuid import uuid4

from swarm.findings import Finding

MAX_SHARE_BPS = 5000  # Arsune's limit for a scout share
MIN_COUNTER_BPS = 500
STEP_BPS = 100
OPEN = {"offered", "countered"}


def percent(bps: int | None) -> str:
    return "-" if bps is None else f"{bps / 100:g}%"


def success_chance(proven: int, resolved: int) -> float:
    """Beta(2, 1) prior: a hunter starts out trusting tips, then learns from its own claims."""
    return (proven + 2) / (resolved + 3)


def max_share_bps(chance: float, reward: int, bond: int, margin: float) -> int:
    """The largest share that still leaves the hunter `margin * reward` of expected value.

    Expected value at share s is chance * reward * (1 - s) - (1 - chance) * bond: a claim that
    is not proven is counted as a lost bond, which is the cautious case.
    """
    share = 1 - (margin * reward + (1 - chance) * bond) / (chance * reward)
    bps = int(share * 10_000) // STEP_BPS * STEP_BPS
    return max(0, min(MAX_SHARE_BPS, bps))


def hunter_offer(ask_bps: int, max_bps: int) -> tuple[str, int | None]:
    if ask_bps <= max_bps:
        return "accept", ask_bps
    if max_bps >= MIN_COUNTER_BPS:
        return "counter", max_bps
    return "walk", None


def scout_accepts(counter_bps: int, floor_bps: int) -> bool:
    return counter_bps >= floor_bps


@dataclass
class Tip:
    scout: str
    scout_account: str
    finding: Finding
    ask_share_bps: int
    id: str = field(default_factory=lambda: str(uuid4()))
    status: str = "offered"
    buyer: str | None = None
    final_share_bps: int | None = None
    note: str | None = None
    negotiating: str | None = None
    countered_by: set = field(default_factory=set)
    passed: set = field(default_factory=set)
    claim_id: str | None = None
    outcome: str | None = None

    @property
    def claim_type(self) -> str:
        return self.finding.claim_type

    def view(self) -> dict:
        return {
            "id": self.id,
            "scout": self.scout,
            "claim_type": self.claim_type,
            "urls": list(self.finding.urls),
            "ask_share_bps": self.ask_share_bps,
            "status": self.status,
            "buyer": self.buyer,
            "final_share_bps": self.final_share_bps,
            "note": self.note,
        }


class TipBoard:
    def __init__(self, log):
        self.tips: dict[str, Tip] = {}
        self.log = log  # log(agent_id, text)

    def post(self, scout: str, scout_account: str, finding: Finding, ask_bps: int) -> Tip:
        tip = Tip(scout, scout_account, finding, ask_bps)
        self.tips[tip.id] = tip
        self.log(
            scout,
            f"{scout} offered a {finding.label} tip for {percent(ask_bps)} of the reward "
            f"({finding.evidence}).",
        )
        return tip

    def is_listed(self, key: str) -> bool:
        return any(t.finding.key == key and t.status in OPEN | {"sold"} for t in self.tips.values())

    def offers_for(self, hunter: str) -> list[Tip]:
        return [
            t
            for t in self.tips.values()
            if t.status == "offered"
            and not t.negotiating
            and hunter not in t.passed
            and hunter not in t.countered_by
        ]

    async def negotiate(self, tip: Tip, hunter: str, max_bps: int, reason: str, scout) -> bool:
        """One round: accept the ask, or counter once and let the scout answer. True if sold."""
        if tip.status != "offered" or tip.negotiating or hunter in tip.countered_by:
            return False
        tip.negotiating = hunter
        try:
            action, share = hunter_offer(tip.ask_share_bps, max_bps)
            label = f"{tip.scout}'s {tip.finding.label} tip"
            if action == "walk":
                tip.passed.add(hunter)
                tip.note = f"{hunter} walked away: {reason}"
                self.log(hunter, f"{hunter} walked away from {label}: {reason}.")
                return False
            if action == "accept":
                self.log(hunter, f"{hunter} bought {label} at the asked {percent(share)}.")
                self._sell(tip, hunter, share, f"sold at the asked {percent(share)}")
                return True
            tip.countered_by.add(hunter)
            tip.status, tip.buyer = "countered", hunter
            tip.note = f"{hunter} offers {percent(share)}: {reason}"
            self.log(
                hunter,
                f"{hunter} countered {label}: {percent(share)} instead of "
                f"{percent(tip.ask_share_bps)}, because {reason}.",
            )
            if await scout.answer(tip, hunter, share):
                self._sell(tip, hunter, share, f"sold to {hunter} after a counter")
                return True
            tip.status, tip.buyer = "declined", None
            tip.note = f"{tip.scout} refused {percent(share)} from {hunter}"
            return False
        finally:
            tip.negotiating = None

    def _sell(self, tip: Tip, hunter: str, share: int, note: str):
        tip.status, tip.buyer, tip.final_share_bps, tip.note = "sold", hunter, share, note

    def withdraw(self, tip: Tip, note: str):
        if tip.status in OPEN:
            tip.status, tip.buyer, tip.note = "declined", None, note

    def close(self, note: str):
        for tip in self.tips.values():
            self.withdraw(tip, note)
