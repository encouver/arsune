"""Create one named NEAR account per swarm agent and fund them inside intents.near (mainnet).

Steps (each is skipped when the chain shows it is already done, so re-running resumes):
  1. <parent> (default arsune-swarm.near): the Arsune account calls near.create_account.
  2. owner, hunter-1..N, scout-1..M as <role>.<parent>: the parent's CreateAccount + AddKey,
     signed by the parent and relayed by Arsune (NEP-366 meta transaction).
  3. each sub-account calls intents.near add_public_key (1 yoctoNEAR), relayed the same way.
  4. one execute_intents from Arsune: one signed message with a USDC transfer to the owner
     and to each hunter (top-up to the target balance).

Arsune pays all gas, so the new accounts hold no NEAR (zero-balance accounts, < 770 bytes).
Keys go to ARSUNE_SWARM_ACCOUNTS_FILE (mode 600) before any account is created.

    uv run python scripts/near_swarm_accounts.py
    uv run python scripts/near_swarm_accounts.py --i-approve-mainnet

Without the flag only read-only RPC calls are made and nothing is written.
"""

import argparse
import base64
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    OutcomeUnknown,
    access_key,
    add_full_access_key_action,
    create_account_action,
    decode_ed25519,
    load_signing_key,
    near_balance,
    upfront_gas_cost,
    view_account,
)
from app.settlement.swarm_accounts import (
    load_swarm_accounts,
    plan_swarm_accounts,
    save_swarm_accounts,
    swarm_roles,
)

REGISTRAR = "near"  # runs near-linkdrop: create_account(new_account_id, new_public_key)
CREATE_PARENT_GAS = 50 * TGAS  # linkdrop reserves 13 Tgas for its callback
FUND_GAS = 50 * TGAS
MAX_TOTAL_NEAR = 12 * 10**22  # 0.12 NEAR
MAX_TOTAL_USDC = 900_000  # 0.9 USDC
YOCTO = 10**24
ACCOUNT_CREATION_CHARGE = 7 * 10**21  # nearcore account_creation_charge: 0.007 NEAR
# nearcore parameters.yaml fees (send + execution, gas units); MARGIN covers per-byte fees
RECEIPT = 2 * 108_059_500_000
DELEGATE = 2 * 200_000_000_000
CREATE_ACCOUNT = 500_000_000_000 + 7_200_000_000_000
ADD_FULL_KEY = 2 * 101_765_125_000
FUNCTION_CALL = 200_000_000_000 + 780_000_000_000
MARGIN = 1 * TGAS
NAME = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)*\.near$")


@dataclass
class Step:
    name: str
    count: int
    gas: int  # gas units per transaction, attached gas included
    extra: int = 0  # yoctoNEAR per transaction on top of gas (creation charge, deposit)

    def burn(self, gas_price: int) -> int:
        """Upper bound of NEAR spent per transaction."""
        return self.gas * gas_price + self.extra

    def upfront(self, gas_price: int) -> int:
        """NEAR the payer must hold when sending one transaction."""
        return upfront_gas_cost(self.gas, gas_price) + self.extra


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def near(yocto: int) -> str:
    return f"{yocto / YOCTO:.6f}"


def show_tx(label: str, tx_hash: str) -> None:
    print(f"  {label}: tx {tx_hash}  https://nearblocks.io/txns/{tx_hash}")


def input_error(args) -> str | None:
    if not settings.near_account_id or not settings.near_private_key.get_secret_value():
        return "set ARSUNE_NEAR_ACCOUNT_ID and ARSUNE_NEAR_PRIVATE_KEY"
    if not NAME.match(args.parent) or len(f"hunter-{args.hunters}.{args.parent}") > 64:
        return f"{args.parent} is not a valid direct <name>.near account"
    if not (0 <= args.hunters <= 20 and 0 <= args.scouts <= 20):
        return "hunters and scouts must be 0..20"
    for amount in (args.owner_usdc, args.hunter_usdc):
        if not 0 <= amount <= settings.max_payment_amount:
            return f"USDC targets must be 0..{settings.max_payment_amount}"
    return None


def read_state(rpc, accounts: dict, contract: str) -> dict:
    """Per role: account exists, our key is on it, intents.near knows the key, USDC balance."""
    state = {}
    for role, account in accounts.items():
        exists = view_account(rpc, account.account_id) is not None
        ours = exists and access_key(rpc, account.account_id, account.public_key) is not None
        registered = (
            role != "parent"
            and ours
            and ni.has_public_key(
                account.account_id, account.public_key, rpc=rpc, contract=contract
            )
        )
        usdc = ni.balance(account.account_id, settings.bounty_token, rpc=rpc, contract=contract)
        state[role] = {"exists": exists, "ours": ours, "registered": registered, "usdc": usdc}
    return state


def top_ups(args, roles: list[str], state: dict) -> dict[str, int]:
    targets = {role: args.hunter_usdc for role in roles if role.startswith("hunter-")}
    targets["owner"] = args.owner_usdc
    return {
        role: target - state[role]["usdc"]
        for role, target in targets.items()
        if target > state[role]["usdc"]
    }


def plan_steps(args, roles: list[str], state: dict, transfers: dict) -> list[Step]:
    steps = []
    if not state["parent"]["exists"]:
        gas = RECEIPT + FUNCTION_CALL + CREATE_PARENT_GAS + MARGIN
        name = f"create {args.parent} via near.create_account"
        steps.append(Step(name, 1, gas, ACCOUNT_CREATION_CHARGE))
    missing = [role for role in roles if not state[role]["exists"]]
    if missing:
        gas = 2 * RECEIPT + DELEGATE + CREATE_ACCOUNT + ADD_FULL_KEY + MARGIN
        steps.append(
            Step("create sub-account (relayed)", len(missing), gas, ACCOUNT_CREATION_CHARGE)
        )
    unregistered = [role for role in roles if not state[role]["registered"]]
    if unregistered:
        gas = 2 * RECEIPT + DELEGATE + FUNCTION_CALL + ni.ADD_KEY_GAS + MARGIN
        steps.append(Step("add_public_key on intents.near (relayed)", len(unregistered), gas, 1))
    if transfers:
        gas = RECEIPT + FUNCTION_CALL + FUND_GAS + MARGIN
        steps.append(Step(f"execute_intents, {len(transfers)} USDC transfers", 1, gas))
    return steps


def print_plan(args, accounts, state, steps, transfers, gas_price, balances) -> tuple[int, int]:
    print(f"Swarm accounts under {args.parent} (mainnet), relayer {settings.near_account_id}")
    for role, account in accounts.items():
        flags = state[role]
        status = "missing" if not flags["exists"] else "ours" if flags["ours"] else "NOT OURS"
        key = "key on intents.near" if flags["registered"] else ""
        print(f"  {account.account_id:<40} {status:<9} {key:<20} USDC {flags['usdc']}")
    print(f"\nCost table (gas price {gas_price} yocto/gas, upper bounds):")
    print(f"  {'step':<48} {'tx':>3} {'NEAR each':>10} {'NEAR':>10} {'held up front':>14}")
    for step in steps:
        each = step.burn(gas_price)
        print(
            f"  {step.name:<48} {step.count:>3} {near(each):>10} {near(each * step.count):>10}"
            f" {near(step.upfront(gas_price)):>14}"
        )
    total_near = sum(step.burn(gas_price) * step.count for step in steps)
    total_usdc = sum(transfers.values())
    print(f"  {'total':<48} {'':>3} {'':>10} {near(total_near):>10}")
    print(f"  USDC transfers: {transfers or 'none'}  total {total_usdc / 10**6:.6f} USDC")
    print(f"  relayer NEAR {near(balances['near'])}, USDC in intents.near {balances['usdc']}")
    return total_near, total_usdc


def budget_error(steps, total_near, total_usdc, gas_price, balances) -> str | None:
    if total_near > MAX_TOTAL_NEAR:
        return f"total NEAR {near(total_near)} is above {near(MAX_TOTAL_NEAR)}"
    if total_usdc > MAX_TOTAL_USDC:
        return f"total USDC {total_usdc} is above {MAX_TOTAL_USDC}"
    peak = max((step.upfront(gas_price) for step in steps), default=0)
    if balances["near"] < total_near + peak:
        return f"relayer needs {near(total_near + peak)} NEAR (spend + largest up-front hold)"
    if balances["usdc"] < total_usdc:
        return "relayer has too little USDC in intents.near"
    return None


def create_parent(rpc, relayer, key, parent) -> None:
    tx_hash, outcome = rpc.call_function(
        relayer,
        key,
        REGISTRAR,
        "create_account",
        {"new_account_id": parent.account_id, "new_public_key": parent.public_key},
        gas=CREATE_PARENT_GAS,
    )
    show_tx(f"created {parent.account_id}", tx_hash)
    created = json.loads(base64.b64decode(outcome["status"]["SuccessValue"]) or b"null")
    if created is not True or access_key(rpc, parent.account_id, parent.public_key) is None:
        raise SettlementError(f"{parent.account_id} was not created with our key")


def create_sub(rpc, relayer, key, parent, account) -> None:
    public_key = decode_ed25519(account.public_key, 32, "public_key")
    tx_hash, _ = rpc.relay(
        relayer,
        key,
        parent.account_id,
        load_signing_key(parent.private_key),
        account.account_id,
        [create_account_action(), add_full_access_key_action(public_key)],
    )
    show_tx(f"created {account.account_id}", tx_hash)
    if access_key(rpc, account.account_id, account.public_key) is None:
        raise SettlementError(f"{account.account_id} has no access key after creation")


def register_key(rpc, relayer, key, account, contract) -> None:
    tx_hash, _ = rpc.relay(
        relayer,
        key,
        account.account_id,
        load_signing_key(account.private_key),
        contract,
        [ni.add_public_key_action(account.public_key)],
    )
    show_tx(f"registered key of {account.account_id}", tx_hash)
    if not ni.has_public_key(account.account_id, account.public_key, rpc=rpc, contract=contract):
        raise SettlementError(f"{contract} does not list the key of {account.account_id}")


def fund(rpc, relayer, private_key, accounts, transfers, targets, contract) -> None:
    intents = [
        {
            "intent": "transfer",
            "receiver_id": accounts[role].account_id,
            "tokens": {settings.bounty_token: str(amount)},
            "memo": f"arsune-swarm-fund:{role}",
        }
        for role, amount in transfers.items()
    ]
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    signed = ni.sign_intents(relayer, private_key, intents, deadline, rpc=rpc, contract=contract)
    ni.simulate([signed], rpc=rpc, contract=contract)
    tx_hash = ni.execute(
        [signed],
        account_id=relayer,
        private_key=private_key,
        rpc=rpc,
        contract=contract,
        gas=FUND_GAS,
    )
    show_tx(f"funded {', '.join(transfers)}", tx_hash)
    for role in transfers:
        balance = ni.balance(accounts[role].account_id, rpc=rpc, contract=contract)
        print(f"  {accounts[role].account_id} USDC in intents.near: {balance}")
        if balance != targets[role]:
            raise SettlementError(
                f"{accounts[role].account_id} holds {balance}, not {targets[role]}"
            )


def execute_plan(rpc, args, accounts, roles, state, transfers) -> None:
    relayer = settings.near_account_id
    key = load_signing_key(settings.near_private_key)
    contract = settings.intents_contract
    if not state["parent"]["exists"]:
        create_parent(rpc, relayer, key, accounts["parent"])
    for role in roles:
        if not state[role]["exists"]:
            create_sub(rpc, relayer, key, accounts["parent"], accounts[role])
    for role in roles:
        if not state[role]["registered"]:
            register_key(rpc, relayer, key, accounts[role], contract)
    if transfers:
        targets = {role: state[role]["usdc"] + amount for role, amount in transfers.items()}
        fund(rpc, relayer, settings.near_private_key, accounts, transfers, targets, contract)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--parent", default="arsune-swarm.near")
    parser.add_argument("--hunters", type=int, default=6)
    parser.add_argument("--scouts", type=int, default=3)
    parser.add_argument("--owner-usdc", type=int, default=500_000, help="target, smallest units")
    parser.add_argument("--hunter-usdc", type=int, default=20_000, help="target, smallest units")
    parser.add_argument("--i-approve-mainnet", action="store_true")
    args = parser.parse_args(argv)

    if error := input_error(args):
        return refuse(error)
    try:
        load_signing_key(settings.near_private_key)
        file = settings.swarm_accounts_file
        existing = load_swarm_accounts(file) if file.exists() else {}
        roles = swarm_roles(args.hunters, args.scouts)
        accounts = plan_swarm_accounts(args.parent, roles, existing)
    except SettlementError as error:
        return refuse(str(error))

    rpc = NearRpc(settings.near_rpc_url)
    contract = settings.intents_contract
    try:
        state = read_state(rpc, accounts, contract)
        gas_price = int(rpc.call("gas_price", [None])["gas_price"])
        balances = {
            "near": near_balance(rpc, settings.near_account_id),
            "usdc": ni.balance(settings.near_account_id, rpc=rpc, contract=contract),
        }
    except SettlementError as error:
        print(f"FAILED to read chain state: {error}")
        return 3
    transfers = top_ups(args, roles, state)
    steps = plan_steps(args, roles, state, transfers)
    total_near, total_usdc = print_plan(
        args, accounts, state, steps, transfers, gas_price, balances
    )

    foreign = [
        account.account_id
        for role, account in accounts.items()
        if state[role]["exists"] and not state[role]["ours"]
    ]
    if foreign:
        return refuse(f"taken: our key is not on {', '.join(foreign)}")
    if error := budget_error(steps, total_near, total_usdc, gas_price, balances):
        return refuse(error)
    if not steps:
        print("Nothing to do: every step is already done on chain.")
        return 0
    if not args.i_approve_mainnet:
        print("Not sent. Re-run with --i-approve-mainnet to run these steps on mainnet.")
        return 1

    print(f"Keys saved to {save_swarm_accounts(accounts, file)} (mode 600)")
    try:
        execute_plan(rpc, args, accounts, roles, state, transfers)
    except OutcomeUnknown as error:
        print(f"UNKNOWN: {error}. Check https://nearblocks.io/txns/{error.tx_hash}, then re-run.")
        return 3
    except SettlementError as error:
        print(f"FAILED: {error}. Re-run to resume; finished steps are skipped.")
        return 3
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
