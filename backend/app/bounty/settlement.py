from app.settlement.base import Settlement
from app.settlement.dry import DrySettlement


def get_settlement() -> Settlement:
    # The NEAR Intents implementation replaces this once it is wired in.
    return DrySettlement()
