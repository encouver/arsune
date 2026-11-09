"""Make one small real USDC payment through NEAR Intents on mainnet.

Prints the plan and balances, then sends only with --i-approve-mainnet:

    uv run python scripts/near_intents_payment.py --receiver <account> --amount 10000
    uv run python scripts/near_intents_payment.py --receiver <account> --amount 10000 \
        --i-approve-mainnet

The amount is in USDC's smallest unit (6 decimals): 10000 = 0.01 USDC.
Without the flag nothing is signed or sent; only read-only RPC calls are made.
"""

import argparse
import sys
from datetime import UTC, datetime

from app.core.config import settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import NearRpcError

HARD_CAP = 1_000_000  # 1 USDC: the approved budget for the first live test
MAX_GAS_COST = 2 * 10**22  # 0.02 NEAR of prepaid gas; unused gas is refunded
YOCTO = 10**24


def near_balance(rpc, account_id: str) -> int:
    try:
        account = rpc.call(
            "query", {"request_type": "view_account", "finality": "final", "account_id": account_id}
        )
    except NearRpcError as error:
        if error.cause == "UNKNOWN_ACCOUNT":
            return 0
        raise
    return int(account["amount"])


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receiver", required=True)
    parser.add_argument("--amount", type=int, required=True, help="USDC smallest units")
    parser.add_argument("--memo", default=f"arsune-live-test-{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
    parser.add_argument("--i-approve-mainnet", action="store_true")
    args = parser.parse_args(argv)

    try:
        settlement = ni.NearIntentsSettlement(settings)
    except SettlementError as error:
        return refuse(str(error))
    if not 0 < args.amount <= min(HARD_CAP, settings.max_payment_amount):
        return refuse(f"amount must be 1..{min(HARD_CAP, settings.max_payment_amount)}")
    if args.receiver == settlement.account_id:
        return refuse("receiver is Arsune's own account")

    rpc = settlement.rpc
    gas_cost = ni.EXECUTE_GAS * int(rpc.call("gas_price", [None])["gas_price"])
    usdc = ni.balance(settlement.account_id, settlement.token, rpc=rpc)
    near = near_balance(rpc, settlement.account_id)
    print("Planned NEAR Intents transfer (mainnet)")
    print(f"  rpc            {settings.near_rpc_url}")
    print(f"  contract       {settlement.contract} execute_intents")
    print(f"  token          {settlement.token}")
    print(f"  from           {settlement.account_id}")
    print(f"  to             {args.receiver}")
    print(f"  amount         {args.amount} ({args.amount / 10**6:.6f} USDC)")
    print(f"  memo           {args.memo}")
    print(f"  attached       {ni.EXECUTE_DEPOSIT} yoctoNEAR")
    print(f"  prepaid gas    {ni.EXECUTE_GAS // 10**12} Tgas, at most {gas_cost / YOCTO:.6f} NEAR")
    print(f"  USDC in intents.near   {usdc} ({usdc / 10**6:.6f} USDC)")
    print(f"  NEAR for gas           {near / YOCTO:.6f} NEAR")

    if ni.EXECUTE_DEPOSIT != 0:
        return refuse("execute_intents must not attach a deposit")
    if gas_cost > MAX_GAS_COST:
        return refuse(f"gas could cost more than {MAX_GAS_COST / YOCTO} NEAR")
    if usdc < args.amount:
        return refuse("not enough USDC in intents.near for this account")
    if near < gas_cost:
        return refuse("not enough NEAR on the relayer account for gas")
    if not args.i_approve_mainnet:
        print("Not sent. Re-run with --i-approve-mainnet to make this real payment.")
        return 1

    try:
        tx_hash = settlement.pay(args.receiver, args.amount, args.memo)
    except SettlementError as error:
        print(f"FAILED: {error}")
        return 3
    print(f"PAID: tx {tx_hash}  https://nearblocks.io/txns/{tx_hash}")
    print(f"  USDC left in intents.near: {ni.balance(settlement.account_id, rpc=rpc)}")
    print(f"  receiver USDC in intents.near: {ni.balance(args.receiver, rpc=rpc)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
