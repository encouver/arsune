"""Agent identities and the transfers they sign.

Agents sign with the same NEP-413 helpers that any NEAR Intents client would use. The salt
always comes from Arsune's `GET /api/settlement`, so signing never calls the NEAR RPC.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.settlement import escrow
from app.settlement.near_intents import sign_transfer
from app.settlement.near_tx import generate_key, implicit_account_id, load_signing_key

INTENT_LIFETIME = timedelta(minutes=5)
ROLES = {"owner", "hunter", "scout"}


@dataclass(frozen=True)
class Identity:
    account_id: str
    private_key: str = field(repr=False)
    role: str = "hunter"

    @classmethod
    def generate(cls, role: str) -> "Identity":
        """A fresh implicit account: the account ID is the hex public key."""
        private_key, _ = generate_key()
        return cls(implicit_account_id(load_signing_key(private_key)), private_key, role)


def load_accounts(path: str | Path | None = None) -> list[Identity]:
    """Live identities from the swarm accounts file that scripts/near_swarm_accounts.py writes.

    Roles there are "owner", "hunter-1", "scout-1", ... ("parent" is not an agent). The
    result is ordered owner, hunters, scouts, each by number.
    """
    from app.settlement.swarm_accounts import load_swarm_accounts

    def order(item) -> tuple[int, int]:
        kind, _, number = item[0].partition("-")
        return ["owner", "hunter", "scout"].index(kind), int(number or 0)

    agents = [item for item in load_swarm_accounts(path).items() if item[0].split("-")[0] in ROLES]
    return [
        Identity(account.account_id, account.private_key.get_secret_value(), role.split("-")[0])
        for role, account in sorted(agents, key=order)
    ]


def bond_memo(claim_id: str) -> str:
    return f"arsune:bond:{claim_id}"


def fund_memo(fund_id: str) -> str:
    return f"arsune:fund:{fund_id}"


def escrow_fund_msg(settlement: dict, bounty_id: str) -> str | None:
    """The escrow `msg` of a funding deposit; None when Arsune does not use the escrow."""
    return escrow.fund_msg(bounty_id) if settlement.get("mode") == "escrow" else None


def escrow_bond_msg(settlement: dict, bounty_id: str, claim_id: str, scout: dict | None):
    if settlement.get("mode") != "escrow":
        return None
    return escrow.bond_msg(bounty_id, claim_id, scout and (scout["account"], scout["share_bps"]))


def sign_payment(
    identity: Identity,
    settlement: dict,
    amount: int,
    memo: str,
    now: datetime | None = None,
    msg: str | None = None,
) -> dict:
    """Sign a transfer of exactly `amount` of the bounty token to Arsune with `memo`. In escrow
    mode it is a deposit to the escrow, and `msg` names the bounty or the claim."""
    salt = settlement.get("salt")
    if not salt:
        raise ValueError("Arsune did not publish a Verifier salt; refusing to fetch it from RPC.")
    deadline = (now or datetime.now(UTC)) + INTENT_LIFETIME
    if settlement.get("mode") == "escrow":
        return escrow.sign_deposit(
            identity.account_id,
            identity.private_key,
            settlement["escrow_contract"],
            settlement["token"],
            amount,
            msg,
            memo,
            deadline,
            salt=bytes.fromhex(salt),
            contract=settlement["intents_contract"],
        )
    return sign_transfer(
        identity.account_id,
        identity.private_key,
        settlement["arsune_account"],
        settlement["token"],
        amount,
        memo,
        deadline,
        salt=bytes.fromhex(salt),
        contract=settlement["intents_contract"],
    )
