"""Select the settlement and describe what clients must sign for it."""

import time
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import uuid4

from sqlalchemy import text

from app.core.config import settings
from app.core.models import LedgerEntry
from app.settlement.base import Settlement
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT, DrySettlement

EXPLORER_TX = "https://nearblocks.io/txns/{}"
SALT_SECONDS = 300
# Time the owner has to approve the signature in the wallet.
FUND_INTENT_TTL = timedelta(minutes=5)
# Every process that sends with Arsune's relayer key takes this PostgreSQL advisory lock.
RELAYER_LOCK = 0x4152_5355_4E45
_salt = {"value": None, "at": 0.0}
_salt_lock = Lock()


def get_settlement() -> Settlement:
    if settings.settlement_mode == "near_intents":
        from app.settlement.near_intents import NearIntentsSettlement

        return NearIntentsSettlement()
    if settings.settlement_mode == "escrow":
        from app.settlement.escrow import EscrowSettlement

        return EscrowSettlement()
    return DrySettlement()


def settlement_mode() -> str:
    # Read views use the setting, so a missing key cannot break pages that move no money.
    return settings.settlement_mode


def arsune_account() -> str:
    return settings.near_account_id or DRY_ACCOUNT


def current_salt() -> str:
    if settings.settlement_mode == "dry":
        return DRY_SALT
    from app.settlement import near_intents

    with _salt_lock:
        if _salt["value"] and time.monotonic() - _salt["at"] < SALT_SECONDS:
            return _salt["value"]
        _salt["value"] = near_intents.current_salt().hex()
        _salt["at"] = time.monotonic()
        return _salt["value"]


def settlement_info() -> dict:
    info = {
        "mode": settlement_mode(),
        "arsune_account": arsune_account(),
        "token": settings.bounty_token,
        "intents_contract": settings.intents_contract,
        "salt": current_salt(),
    }
    if settlement_mode() == "escrow":
        # Deposits go to the escrow with a `msg` (fund_msg, bond_msg); Arsune is the judge.
        info["escrow_contract"] = settings.escrow_contract
    return info


def explorer_url(mode: str, tx_id: str | None) -> str | None:
    return EXPLORER_TX.format(tx_id) if mode != "dry" and tx_id else None


def fund_msg(bounty_id: str) -> str | None:
    """The `msg` a funding deposit must carry, or None when deposits have no `msg`."""
    if settings.settlement_mode != "escrow":
        return None
    from app.settlement import escrow

    return escrow.fund_msg(bounty_id)


def transfer_payload(*, sender: str, amount: int, memo: str, msg: str | None = None) -> dict:
    """The unsigned NEP-413 payload a wallet signs so `check_transfer` accepts it:
    {message, nonce (base64), recipient}, with a deadline FUND_INTENT_TTL ahead."""
    from app.settlement import near_intents

    if settings.settlement_mode == "escrow":
        from app.settlement.escrow import deposit_intent

        intent = deposit_intent(settings.escrow_contract, settings.bounty_token, amount, msg, memo)
    else:
        intent = near_intents.transfer_intent(arsune_account(), settings.bounty_token, amount, memo)
    deadline = datetime.now(UTC) + FUND_INTENT_TTL
    payload = near_intents.nep413_payload(
        sender,
        [intent],
        deadline,
        salt=bytes.fromhex(current_salt()),
        contract=settings.intents_contract,
    )
    return {**payload, "memo": memo, "deadline": near_intents.format_deadline(deadline)}


def bond_msg(bounty_id: str, claim_id: str, scout: dict | None) -> str | None:
    if settings.settlement_mode != "escrow":
        return None
    from app.settlement import escrow

    share = (scout["account"], scout["share_bps"]) if scout else None
    return escrow.bond_msg(bounty_id, claim_id, share)


def check_transfer(signed: dict, *, sender: str, amount: int, memo: str, msg: str | None = None):
    """Reject a signed transfer that does not pay exactly this amount to Arsune with this memo
    (in escrow mode: to the escrow, with this `msg`)."""
    from app.settlement.near_intents import verify_signed_transfer

    if settings.settlement_mode == "escrow":
        from app.settlement.escrow import verify_deposit

        verify_deposit(
            signed,
            sender=sender,
            escrow=settings.escrow_contract,
            token_id=settings.bounty_token,
            amount=amount,
            msg=msg,
            memo=memo,
            contract=settings.intents_contract,
        )
        return
    verify_signed_transfer(
        signed,
        sender=sender,
        receiver=arsune_account(),
        token_id=settings.bounty_token,
        amount=amount,
        memo=memo,
        contract=settings.intents_contract,
    )


def key_registered(signed: dict, *, sender: str) -> bool:
    """Whether NEAR Intents knows the signing key: a named account must add it once."""
    if settings.settlement_mode == "dry":
        return True
    from app.settlement import near_intents

    return near_intents.has_public_key(
        sender, signed["public_key"], contract=settings.intents_contract
    )


@contextmanager
def relayer_lock(mode: str):
    """Serialize real relayer transactions across API and worker processes.

    One key has one access-key nonce sequence; parallel senders would reject each other.
    """
    if mode == "dry":
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


def collect(signed: dict, *, sender: str, amount: int, memo: str, msg: str | None = None) -> str:
    port = get_settlement()
    extra = {"msg": msg} if msg is not None else {}
    with relayer_lock(port.mode):
        return port.collect(signed, sender=sender, amount=amount, memo=memo, **extra)


def pay(receiver: str, amount: int, memo: str) -> str:
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.pay(receiver, amount, memo)


def open_on_escrow(bounty) -> str | None:
    """Create the bounty on the escrow once; return the transaction ID if this call made it."""
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.open_bounty(port.terms(bounty))


def escrow_row(bounty, kind, amount, account, tx_id):
    """A ledger row for an escrow call that is not a claim payment."""
    return LedgerEntry(
        id=str(uuid4()),
        bounty_id=bounty.id,
        kind=kind,
        amount=amount,
        account=account,
        memo=f"arsune:{kind}:{bounty.id}",
        tx_id=tx_id,
        settlement_mode="escrow",
        status="completed",
    )


def rule(claim_id: str, outcome: str, evidence_hash: str):
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.rule(claim_id, outcome, evidence_hash)


def withdraw(bounty_id: str):
    port = get_settlement()
    with relayer_lock(port.mode):
        return port.withdraw(bounty_id)
