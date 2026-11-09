"""Select the settlement and describe what clients must sign for it."""

import time
from contextlib import contextmanager
from threading import Lock

from sqlalchemy import text

from app.core.config import settings
from app.settlement.base import Settlement
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT, DrySettlement

EXPLORER_TX = "https://nearblocks.io/txns/{}"
SALT_SECONDS = 300
# Every process that sends with Arsune's relayer key takes this PostgreSQL advisory lock.
RELAYER_LOCK = 0x4152_5355_4E45
_salt = {"value": None, "at": 0.0}
_salt_lock = Lock()


def get_settlement() -> Settlement:
    if settings.settlement_mode == "near_intents":
        from app.settlement.near_intents import NearIntentsSettlement

        return NearIntentsSettlement()
    return DrySettlement()


def settlement_mode() -> str:
    # Read views use the setting, so a missing key cannot break pages that move no money.
    return settings.settlement_mode


def arsune_account() -> str:
    return settings.near_account_id or DRY_ACCOUNT


def current_salt() -> str:
    if settings.settlement_mode != "near_intents":
        return DRY_SALT
    from app.settlement import near_intents

    with _salt_lock:
        if _salt["value"] and time.monotonic() - _salt["at"] < SALT_SECONDS:
            return _salt["value"]
        _salt["value"] = near_intents.current_salt().hex()
        _salt["at"] = time.monotonic()
        return _salt["value"]


def settlement_info() -> dict:
    return {
        "mode": settlement_mode(),
        "arsune_account": arsune_account(),
        "token": settings.bounty_token,
        "intents_contract": settings.intents_contract,
        "salt": current_salt(),
    }


def explorer_url(mode: str, tx_id: str | None) -> str | None:
    return EXPLORER_TX.format(tx_id) if mode == "near_intents" and tx_id else None


def check_transfer(signed: dict, *, sender: str, amount: int, memo: str):
    """Reject a signed transfer that does not pay exactly this amount to Arsune with this memo."""
    from app.settlement.near_intents import verify_signed_transfer

    verify_signed_transfer(
        signed,
        sender=sender,
        receiver=arsune_account(),
        token_id=settings.bounty_token,
        amount=amount,
        memo=memo,
        contract=settings.intents_contract,
    )


@contextmanager
def relayer_lock(mode: str):
    """Serialize real relayer transactions across API and worker processes.

    One key has one access-key nonce sequence; parallel senders would reject each other.
    """
    if mode != "near_intents":
        yield
        return
    from app.core.db import engine

    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": RELAYER_LOCK})
        try:
            yield
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RELAYER_LOCK})
            connection.commit()


def collect(signed: dict, *, sender: str, amount: int, memo: str) -> str:
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.collect(signed, sender=sender, amount=amount, memo=memo)


def pay(receiver: str, amount: int, memo: str) -> str:
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.pay(receiver, amount, memo)
