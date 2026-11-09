"""Swarm agent accounts: one named NEAR sub-account per agent under one parent.

scripts/near_swarm_accounts.py creates them and writes the keys to
`ARSUNE_SWARM_ACCOUNTS_FILE` (default .data/swarm-accounts.json, mode 600).
The swarm runner loads them with `load_swarm_accounts()`.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from pydantic import SecretStr

from app.core.config import settings
from app.settlement.base import SettlementError
from app.settlement.near_tx import generate_key

FILE_VERSION = 1


@dataclass(frozen=True)
class SwarmAccount:
    role: str
    account_id: str
    public_key: str
    private_key: SecretStr


def swarm_roles(hunters: int, scouts: int) -> list[str]:
    return (
        ["owner"]
        + [f"hunter-{i}" for i in range(1, hunters + 1)]
        + [f"scout-{i}" for i in range(1, scouts + 1)]
    )


def _path(path: Path | str | None) -> Path:
    return Path(path) if path is not None else settings.swarm_accounts_file


def load_swarm_accounts(path: Path | str | None = None) -> dict[str, SwarmAccount]:
    """Accounts by role ("parent", "owner", "hunter-1", ...). Raises if the file is missing."""
    file = _path(path)
    try:
        data = json.loads(file.read_text())
    except (OSError, ValueError) as error:
        raise SettlementError(f"cannot read swarm accounts from {file}: {error}") from error
    if data.get("version") != FILE_VERSION:
        raise SettlementError(f"{file} has an unknown format version")
    return {
        entry["role"]: SwarmAccount(
            entry["role"],
            entry["account_id"],
            entry["public_key"],
            SecretStr(entry["private_key"]),
        )
        for entry in data["accounts"]
    }


def save_swarm_accounts(accounts: dict[str, SwarmAccount], path: Path | str | None = None) -> Path:
    """Write atomically with mode 600; keys are never printed."""
    file = _path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": FILE_VERSION,
        "network": "mainnet",
        "accounts": [
            {
                "role": account.role,
                "account_id": account.account_id,
                "public_key": account.public_key,
                "private_key": account.private_key.get_secret_value(),
            }
            for account in accounts.values()
        ],
    }
    temporary = file.with_name(file.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        json.dump(data, handle, indent=2)
    os.chmod(temporary, 0o600)
    os.replace(temporary, file)
    return file


def plan_swarm_accounts(
    parent: str, roles: list[str], existing: dict[str, SwarmAccount] | None = None
) -> dict[str, SwarmAccount]:
    """Keep every existing key (even for roles not requested now); add a fresh key for each
    missing role, the parent included."""
    existing = dict(existing or {})
    if "parent" in existing and existing["parent"].account_id != parent:
        raise SettlementError(
            f"the accounts file is for {existing['parent'].account_id}, not {parent}"
        )
    accounts = {}
    for role in ["parent"] + roles:
        account_id = parent if role == "parent" else f"{role}.{parent}"
        if role in existing:
            if existing[role].account_id != account_id:
                raise SettlementError(f"{role} in the accounts file is not {account_id}")
            accounts[role] = existing.pop(role)
            continue
        private_key, public_key = generate_key()
        accounts[role] = SwarmAccount(role, account_id, public_key, SecretStr(private_key))
    return accounts | existing
