"""Deploy the Arsune bounty escrow to NEAR mainnet as a locked contract.

    contracts/escrow/build.sh
    uv run python scripts/near_escrow_deploy.py                      # plan only, read-only RPC
    uv run python scripts/near_escrow_deploy.py --i-approve-mainnet  # one mainnet transaction

One transaction creates escrow.<parent>, sends it the NEAR for its storage, deploys the wasm
and calls new(intents_contract, token_id). It has no AddKey action, so the escrow account never
has an access key: nobody, Arsune included, can sign for it or replace its code. Only the
contract code moves its money (docs/ESCROW.md).

<parent> defaults to the swarm parent (arsune-swarm.near) from ARSUNE_SWARM_ACCOUNTS_FILE. The
parent signs the actions as a NEP-366 delegate action; Arsune relays it and pays the gas and the
storage NEAR (the relayer pays the deposits of delegated actions). If --parent is Arsune's own
named account, Arsune sends the actions directly.

The escrow has no global judge: each bounty names its judge in create_bounty. Arsune creates
bounties with judge = ARSUNE_NEAR_ACCOUNT_ID. Without --i-approve-mainnet only read-only RPC
calls are made and nothing is signed.
"""

import argparse
import hashlib
import json
import struct
import sys
from pathlib import Path

import base58
from app.core.config import settings
from app.settlement import escrow
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    NearRpcError,
    access_key,
    create_account_action,
    function_call_action,
    load_signing_key,
    near_balance,
    public_key_text,
    transfer_action,
    upfront_gas_cost,
    view_account,
)
from app.settlement.swarm_accounts import load_swarm_accounts

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WASM = ROOT / "contracts/escrow/target/near/arsune_escrow.wasm"
YOCTO = 10**24
EXPORTS = {
    "memory",
    "__contract_abi",
    "contract_source_metadata",
    "new",
    "create_bounty",
    "mt_on_transfer",
    "rule",
    "reclaim_bond",
    "close_bounty",
    "withdraw_remaining",
    "retry_payout",
    "on_payout",
    "get_config",
    "get_bounty",
    "get_claim",
    "list_claims",
    "get_owed",
}
INIT_GAS = 10 * TGAS
# Live mainnet runtime config (EXPERIMENTAL_protocol_config, protocol 88), gas units.
STORAGE_BYTE_COST = 10**19
ACCOUNT_BYTES = 100  # storage_usage_config.num_bytes_account
STATE_BYTES = 400  # upper bound of the state that new() writes (STATE record + 40 extra bytes)
ACCOUNT_CREATION_CHARGE = 7 * 10**21  # 0.007 NEAR
RECEIPT = 2 * 108_059_500_000
DELEGATE = 2 * 200_000_000_000
CREATE_ACCOUNT = 500_000_000_000 + 7_200_000_000_000
TRANSFER = 2 * 115_123_062_500
DEPLOY = 2 * 184_765_750_000
# Per code byte: sent once in the transaction and once more in the delegated receipt
# (send_not_sir 47_683_715 each), executed once (64_572_944).
DEPLOY_PER_BYTE = 2 * 47_683_715 + 64_572_944
FUNCTION_CALL = 200_000_000_000 + 780_000_000_000
MARGIN = 5 * TGAS
MAX_DEFAULT_NEAR = 3 * YOCTO


def near(yocto: int) -> str:
    return f"{yocto / YOCTO:.6f}"


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def wasm_exports(code: bytes) -> set[str]:
    """Export names of a wasm module (export section only)."""

    def leb(at: int) -> tuple[int, int]:
        value = shift = 0
        while True:
            byte = code[at]
            at += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value, at

    if code[:4] != b"\0asm":
        raise SettlementError("the file is not a wasm module")
    at, names = 8, set()
    while at < len(code):
        section, at = code[at], at + 1
        size, at = leb(at)
        if section == 7:
            count, pos = leb(at)
            for _ in range(count):
                length, pos = leb(pos)
                names.add(code[pos : pos + length].decode())
                _, pos = leb(pos + length + 1)
        at += size
    return names


def deploy_contract_action(code: bytes) -> bytes:
    return b"\x01" + struct.pack("<I", len(code)) + code


def init_action(intents: str, token: str) -> bytes:
    args = json.dumps({"intents_contract": intents, "token_id": token}, separators=(",", ":"))
    return function_call_action("new", args.encode(), INIT_GAS, 0)


def deploy_gas(code_size: int) -> int:
    """Upper bound of all gas the deploy transaction can burn, attached gas included."""
    return (
        2 * RECEIPT
        + DELEGATE
        + CREATE_ACCOUNT
        + TRANSFER
        + DEPLOY
        + DEPLOY_PER_BYTE * code_size
        + FUNCTION_CALL
        + INIT_GAS
        + MARGIN
    )


def escrow_storage(code_size: int, spare: int) -> int:
    """NEAR the escrow account needs: its code, account record and state, plus spare NEAR
    for the 1 yoctoNEAR of each payout and for owed-payout records."""
    return (code_size + ACCOUNT_BYTES + STATE_BYTES) * STORAGE_BYTE_COST + spare


def access_keys(rpc: NearRpc, account_id: str) -> list:
    result = rpc.call(
        "query",
        {"request_type": "view_access_key_list", "finality": "final", "account_id": account_id},
    )
    return result.get("keys", [])


def signer_for(parent: str):
    """(key of the parent, relayed?) -- Arsune's own account signs directly."""
    if parent == settings.near_account_id:
        return load_signing_key(settings.near_private_key), False
    accounts = load_swarm_accounts()
    account = accounts.get("parent")
    if account is None or account.account_id != parent:
        raise SettlementError(f"the swarm accounts file has no key for {parent}")
    return load_signing_key(account.private_key), True


def print_plan(args, info: dict) -> None:
    print(f"Escrow deploy plan (mainnet), relayer {settings.near_account_id}")
    print(f"  escrow account   {info['escrow_id']} (new, no access key, locked)")
    print(
        f"  parent           {args.parent} ({'relayed by Arsune' if info['relayed'] else 'direct'})"
    )
    print(f"  intents contract {args.intents}")
    print(f"  token            {args.token}")
    print(f"  wasm             {args.wasm}")
    print(f"    sha256         {info['sha256']}")
    print(f"    code_hash      {info['code_hash']}")
    print(f"    size           {info['size']} bytes")
    print(f"  bounties name their judge; Arsune will use judge = {settings.near_account_id}")
    gas, price = info["gas"], info["gas_price"]
    rows = [
        (f"escrow storage: {info['size']} code + {ACCOUNT_BYTES + STATE_BYTES} other bytes",
         info["storage"] - args.spare),
        ("escrow spare NEAR (1 yocto per payout, owed records)", args.spare),
        ("account creation charge", ACCOUNT_CREATION_CHARGE),
        (f"gas, upper bound: {gas / TGAS:.1f} Tgas at {price} yocto/gas", gas * price),
    ]  # fmt: skip
    print("\nCost table (NEAR):")
    for label, amount in rows:
        print(f"  {label:<60} {near(amount):>10}")
    print(f"  {'total spent by the relayer':<60} {near(info['total']):>10}")
    print(f"  {'held up front for gas (refunded down to use)':<60} {near(info['upfront']):>10}")
    print(f"  relayer NEAR balance {near(info['balance'])}")
    deposit = escrow.storage_deposit(args.claims)
    print(
        f"\nEach bounty: create_bounty attaches {near(deposit)} NEAR for the record and "
        f"{args.claims} claims ({escrow.CLAIM_STORAGE_BYTES} bytes each, "
        f"{near(escrow.CLAIM_STORAGE_BYTES * STORAGE_BYTE_COST)} NEAR). Unused claim storage "
        "goes back to the creator at withdraw_remaining; used claim records stay as the public "
        "record."
    )


def deploy(rpc, args, info, parent_key) -> int:
    code = info["code"]
    actions = [
        create_account_action(),
        transfer_action(info["storage"]),
        deploy_contract_action(code),
        init_action(args.intents, args.token),
    ]
    relayer_key = load_signing_key(settings.near_private_key)
    if info["relayed"]:
        tx_hash, _ = rpc.relay(
            settings.near_account_id, relayer_key, args.parent, parent_key, info["escrow_id"],
            actions,
        )  # fmt: skip
    else:
        tx_hash, _ = rpc.send_actions(args.parent, parent_key, info["escrow_id"], actions)
    print(f"Sent: tx {tx_hash}  https://nearblocks.io/txns/{tx_hash}")
    account = view_account(rpc, info["escrow_id"])
    if account is None or account["code_hash"] != info["code_hash"]:
        print("FAILED: the escrow account does not hold the expected code")
        return 3
    if access_keys(rpc, info["escrow_id"]):
        print("FAILED: the escrow account has an access key")
        return 3
    config = rpc.view(info["escrow_id"], "get_config", {})
    if config["intents_contract"] != args.intents or config["token_id"] != args.token:
        print(f"FAILED: unexpected escrow config {config}")
        return 3
    print(f"Deployed and locked: {info['escrow_id']} code_hash {info['code_hash']}, no keys.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wasm", type=Path, default=DEFAULT_WASM)
    parser.add_argument("--parent", default="arsune-swarm.near")
    parser.add_argument("--name", default="escrow")
    parser.add_argument("--intents", default=settings.intents_contract)
    parser.add_argument("--token", default=settings.bounty_token)
    parser.add_argument("--spare", type=int, default=5 * 10**22, help="yoctoNEAR, default 0.05")
    parser.add_argument("--max-near", type=int, default=MAX_DEFAULT_NEAR, help="yoctoNEAR")
    parser.add_argument("--claims", type=int, default=150, help="claims per bounty, for the plan")
    parser.add_argument("--i-approve-mainnet", action="store_true")
    args = parser.parse_args(argv)

    if not settings.near_account_id or not settings.near_private_key.get_secret_value():
        return refuse("set ARSUNE_NEAR_ACCOUNT_ID and ARSUNE_NEAR_PRIVATE_KEY")
    if not args.wasm.exists():
        return refuse(f"{args.wasm} is missing: run contracts/escrow/build.sh")
    code = args.wasm.read_bytes()
    try:
        exports = wasm_exports(code)
        parent_key, relayed = signer_for(args.parent)
    except (SettlementError, IndexError, UnicodeDecodeError) as error:
        return refuse(str(error) or "the wasm file is malformed")
    if exports != EXPORTS:
        return refuse(f"unexpected wasm exports: {sorted(exports ^ EXPORTS)}")
    digest = hashlib.sha256(code).digest()
    info = {
        "code": code,
        "size": len(code),
        "sha256": digest.hex(),
        "code_hash": base58.b58encode(digest).decode(),
        "escrow_id": f"{args.name}.{args.parent}",
        "relayed": relayed,
        "storage": escrow_storage(len(code), args.spare),
        "gas": deploy_gas(len(code)),
    }
    if len(info["escrow_id"]) > 64:
        return refuse(f"{info['escrow_id']} is longer than 64 characters")

    rpc = NearRpc(settings.near_rpc_url)
    try:
        info["gas_price"] = int(rpc.call("gas_price", [None])["gas_price"])
        info["balance"] = near_balance(rpc, settings.near_account_id)
        taken = view_account(rpc, info["escrow_id"]) is not None
        parent_ok = access_key(rpc, args.parent, public_key_text(parent_key)) is not None
        intents_ok = view_account(rpc, args.intents) is not None
    except (SettlementError, NearRpcError) as error:
        print(f"FAILED to read chain state: {error}")
        return 3
    info["total"] = info["storage"] + ACCOUNT_CREATION_CHARGE + info["gas"] * info["gas_price"]
    info["upfront"] = upfront_gas_cost(info["gas"], info["gas_price"])
    print_plan(args, info)

    if taken:
        return refuse(f"{info['escrow_id']} already exists")
    if not parent_ok:
        return refuse(f"our key is not on {args.parent}")
    if not intents_ok:
        return refuse(f"{args.intents} does not exist")
    if info["total"] > args.max_near:
        return refuse(f"total {near(info['total'])} NEAR is above --max-near {near(args.max_near)}")
    if info["balance"] < info["total"] + info["upfront"]:
        need = near(info["total"] + info["upfront"])
        return refuse(f"the relayer needs {need} NEAR (spend + up-front gas hold)")
    if not args.i_approve_mainnet:
        print("\nNot sent. Re-run with --i-approve-mainnet to deploy on mainnet.")
        return 0
    try:
        return deploy(rpc, args, info, parent_key)
    except SettlementError as error:
        print(f"FAILED: {error}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
