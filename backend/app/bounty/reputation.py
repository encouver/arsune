"""Hunter reputation from claim history on all bounties, and the bond it sets.

Only decided claims count. A proven claim (paid or unfunded) is a correct claim; a rejected
claim is a false one. Duplicates, unverified claims, and pending claims are neutral: they do
not show whether the hunter's claims are true.
"""

from fractions import Fraction

from sqlalchemy import func, select

from app.core.config import settings
from app.core.models import Claim

COUNTS = ("proven", "rejected", "duplicate", "unverified", "unfunded")
# A hunter stays "new" and pays the base bond until this many claims are proven or rejected,
# so one early mistake or one lucky claim does not change the price.
MIN_DECIDED = 3
NEW_TIER = ("new", 10_000)
# (tier, lowest score, bond multiplier in basis points), checked in this order.
TIERS = (
    ("trusted", Fraction(3, 4), 5_000),
    ("standard", Fraction(1, 2), 10_000),
    ("poor", Fraction(3, 10), 20_000),
    ("very_poor", Fraction(0), 30_000),
)


def history(session, accounts) -> dict[str, dict]:
    """Outcome counts for each account over its claims on every bounty."""
    found = {account: dict.fromkeys(COUNTS, 0) for account in accounts}
    if not found:
        return found
    holds_fault = Claim.final_fingerprint.is_not(None).label("holds_fault")
    rows = session.execute(
        select(Claim.hunter_account, Claim.outcome, holds_fault, func.count())
        .where(Claim.hunter_account.in_(list(found)), Claim.outcome.is_not(None))
        .group_by(Claim.hunter_account, Claim.outcome, holds_fault)
    )
    for account, outcome, holds, count in rows:
        # A proven claim that holds no fault was proven after the budget was spent.
        if outcome == "proven" and not holds:
            outcome = "unfunded"
        if outcome in COUNTS:
            found[account][outcome] += count
    return found


def score(counts) -> Fraction:
    """Laplace estimate of the chance that the hunter's next decided claim is correct."""
    correct = counts["proven"] + counts["unfunded"]
    return Fraction(correct + 1, correct + counts["rejected"] + 2)


def tier_for(counts) -> tuple[str, int]:
    """Return (tier, bond multiplier in basis points)."""
    if counts["proven"] + counts["unfunded"] + counts["rejected"] < MIN_DECIDED:
        return NEW_TIER
    value = score(counts)
    return next((name, bps) for name, lowest, bps in TIERS if value >= lowest)


def reputation_view(counts) -> dict:
    return {"score": round(float(score(counts)), 4), **counts}


def required_bond(reward_amount: int, base_bond: int, multiplier_bps: int) -> int:
    """The base bond times the multiplier, rounded up to a whole token unit, at least 1.

    In near_intents mode the reward transfer also returns the bond, and one transfer may not
    exceed ARSUNE_MAX_PAYMENT_AMOUNT, so the bond is capped at that limit minus the reward.
    """
    bond = -(-base_bond * multiplier_bps // 10_000)
    if settings.settlement_mode == "near_intents":
        bond = min(bond, settings.max_payment_amount - reward_amount)
    return max(1, bond)


def quote(bounty, account: str, counts: dict) -> dict:
    tier, bps = tier_for(counts)
    return {
        "hunter": account,
        "base_bond": bounty.bond_amount,
        "required_bond": required_bond(bounty.reward_amount, bounty.bond_amount, bps),
        "multiplier": bps / 10_000,
        "tier": tier,
        "reputation": reputation_view(counts),
    }


def bond_quotes(session, bounty, accounts) -> dict[str, dict]:
    return {
        account: quote(bounty, account, counts)
        for account, counts in history(session, accounts).items()
    }
