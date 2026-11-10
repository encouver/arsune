"""When a bounty closes because its budget ran out, and when a top-up opens it again."""

from app.bounty.claims import fee_for

# Why a bounty closed. Only this reason lets a top-up open the bounty again.
BUDGET_SPENT = "budget_spent"


def pays_one_reward(bounty) -> bool:
    return bounty.budget_amount >= bounty.reward_amount + fee_for(
        bounty.reward_amount, bounty.fee_bps
    )


def close_for_budget(bounty, now) -> None:
    """Close an open bounty. A bounty that is already closed keeps its first reason."""
    if bounty.status == "open":
        bounty.status, bounty.closed_reason, bounty.updated_at = "closed", BUDGET_SPENT, now


def takes_funds(bounty) -> bool:
    """An open bounty, or one that closed only because its budget ran out."""
    return bounty.status == "open" or (
        bounty.status == "closed" and bounty.closed_reason == BUDGET_SPENT
    )


def add_funds(bounty, amount, now) -> bool:
    """Add a top-up to the budget. Return True when the top-up opens the bounty again."""
    bounty.budget_amount += amount
    bounty.updated_at = now
    reopen = bounty.status == "closed" and bounty.closed_reason == BUDGET_SPENT
    if reopen and pays_one_reward(bounty):
        bounty.status, bounty.closed_reason = "open", None
        return True
    return False
