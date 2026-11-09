"""Move native USDC from the Arsune NEAR account into its intents.near balance.

The Arsune key signs `ft_transfer_call` on the USDC contract to intents.near
(msg "", 1 yoctoNEAR). Prints the plan and balances, then sends only with
--i-approve-mainnet:

    uv run python scripts/near_deposit_usdc.py --amount 1000000
    uv run python scripts/near_deposit_usdc.py --amount 1000000 --i-approve-mainnet

The amount is in USDC's smallest unit (6 decimals): 1000000 = 1 USDC.
Without the flag nothing is signed or sent; only read-only RPC calls are made.
"""

import argparse
import base64
import json
import sys

from app.core.config import settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    OutcomeUnknown,
    load_signing_key,
    near_balance,
    upfront_gas_cost,
)

HARD_CAP = 1_000_000  # 1 USDC: the approved budget for the first live test
DEPOSIT_GAS = 50 * TGAS  # ft_transfer_call needs >30 Tgas; deposits burn about 9 Tgas
ONE_YOCTO = 1  # NEP-141 requires exactly 1 yoctoNEAR on ft_transfer_call
MAX_GAS_COST = 2 * 10**22  # 0.02 NEAR of prepaid gas; unused gas is refunded
YOCTO = 10**24


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def usdc(units: int) -> str:
    return f"{units} ({units / 10**6:.6f} USDC)"


def storage_fix(rpc, token: str, account_id: str) -> str:
    deposit = int(rpc.view(token, "storage_balance_bounds", {})["min"])
    args = json.dumps({"account_id": account_id, "registration_only": True})
    return (
        f"{account_id} has no USDC storage registration on {token}.\n"
        f"Fix: from any funded NEAR account, call {token} storage_deposit with {args}\n"
        f"and attach {deposit / YOCTO} NEAR ({deposit} yoctoNEAR), for example:\n"
        f"  near contract call-function as-transaction {token} storage_deposit "
        f"json-args '{args}' prepaid-gas '30.0 Tgas' attached-deposit '{deposit / YOCTO} NEAR' "
        f"sign-as <payer-account> network-config mainnet sign-with-keychain send"
    )


def used_amount(outcome: dict) -> int | None:
    """ft_transfer_call returns the amount the receiver kept (the rest is refunded)."""
    try:
        return int(json.loads(base64.b64decode(outcome["status"]["SuccessValue"])))
    except (KeyError, TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--amount", type=int, required=True, help="USDC smallest units")
    parser.add_argument("--i-approve-mainnet", action="store_true")
    args = parser.parse_args(argv)

    account_id = settings.near_account_id
    if not account_id or not settings.near_private_key.get_secret_value():
        return refuse("set ARSUNE_NEAR_ACCOUNT_ID and ARSUNE_NEAR_PRIVATE_KEY")
    if not settings.bounty_token.startswith("nep141:"):
        return refuse("ARSUNE_BOUNTY_TOKEN must be a nep141: token")
    try:
        key = load_signing_key(settings.near_private_key)
    except SettlementError as error:
        return refuse(str(error))
    if not 0 < args.amount <= HARD_CAP:
        return refuse(f"amount must be 1..{HARD_CAP}")

    token = settings.bounty_token.removeprefix("nep141:")
    contract = settings.intents_contract
    rpc = NearRpc(settings.near_rpc_url)
    gas_price = int(rpc.call("gas_price", [None])["gas_price"])
    gas_cost = DEPOSIT_GAS * gas_price
    upfront = upfront_gas_cost(DEPOSIT_GAS, gas_price) + ONE_YOCTO
    registered = rpc.view(token, "storage_balance_of", {"account_id": account_id}) is not None
    wallet = int(rpc.view(token, "ft_balance_of", {"account_id": account_id}))
    near = near_balance(rpc, account_id)
    intents_before = ni.balance(account_id, settings.bounty_token, rpc=rpc, contract=contract)
    call_args = {"receiver_id": contract, "amount": str(args.amount), "msg": ""}
    print("Planned USDC deposit into NEAR Intents (mainnet)")
    print(f"  rpc            {settings.near_rpc_url}")
    print(f"  signer         {account_id}")
    print(f"  call           {token} ft_transfer_call {json.dumps(call_args)}")
    print(f"  attached       {ONE_YOCTO} yoctoNEAR")
    print(f"  prepaid gas    {DEPOSIT_GAS // TGAS} Tgas, burns at most {gas_cost / YOCTO:.6f} NEAR")
    print(f"  held up front  {upfront / YOCTO:.6f} NEAR (refunded down to the gas burnt)")
    print(f"  amount         {usdc(args.amount)}")
    print(f"  USDC on account (FT contract)   {usdc(wallet)}")
    print(f"  USDC in {contract}       {usdc(intents_before)}")
    print(f"  NEAR for gas                    {near / YOCTO:.6f} NEAR")

    if not registered:
        return refuse(storage_fix(rpc, token, account_id))
    if args.amount > wallet:
        return refuse("amount is above the account's USDC balance on the FT contract")
    if gas_cost > MAX_GAS_COST:
        return refuse(f"gas could cost more than {MAX_GAS_COST / YOCTO} NEAR")
    if near < upfront:
        return refuse("not enough NEAR on the account for gas")
    if not args.i_approve_mainnet:
        print("Not sent. Re-run with --i-approve-mainnet to make this real deposit.")
        return 1

    try:
        tx_hash, outcome = rpc.call_function(
            account_id,
            key,
            token,
            "ft_transfer_call",
            call_args,
            gas=DEPOSIT_GAS,
            deposit=ONE_YOCTO,
        )
    except OutcomeUnknown as error:
        print(
            f"UNKNOWN: {error}. Check https://nearblocks.io/txns/{error.tx_hash} before retrying."
        )
        return 3
    except SettlementError as error:
        print(f"FAILED: {error}")
        return 3
    print(f"tx {tx_hash}  https://nearblocks.io/txns/{tx_hash}")
    kept = used_amount(outcome)
    intents_after = ni.balance(account_id, settings.bounty_token, rpc=rpc, contract=contract)
    print(f"  {contract} kept   {kept}")
    print(f"  USDC in {contract} now {usdc(intents_after)}")
    if kept != args.amount or intents_after - intents_before != args.amount:
        print("FAILED: the deposit was not fully credited; check the transaction above.")
        return 3
    print("DEPOSITED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
