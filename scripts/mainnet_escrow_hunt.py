"""One small real hunt on NEAR mainnet through the locked escrow `escrow.arsune.near`.

The Arsune relayer (ARSUNE_NEAR_ACCOUNT_ID) is the judge and the fee account. The script makes
two fresh implicit accounts, an owner and a hunter, and keeps their keys in
.data/mainnet-escrow-hunt.json (mode 600). Implicit accounts sign intents with their own key
and need no registration on intents.near (docs/SETTLEMENT.md).

1. The relayer sends the owner 0.03 NEAR (the owner signs close_bounty) and, with one signed
   intents transfer, 0.30 USDC to the owner and 0.05 USDC to the hunter inside intents.near.
2. Through the Arsune API (escrow mode): the owner opens a bounty on the demo site, the demo
   proof is published, Arsune checks the site and creates the bounty on the escrow, and the
   owner funds 0.30 USDC with a signed deposit.
3. The hunter claims a broken link (proven: reward 0.10 USDC, fee 0.01 USDC to Arsune) and a
   link that works (rejected: the 0.01 USDC bond goes to the bounty balance).
4. The owner signs close_bounty, Arsune sends withdraw_remaining (the rest goes to the owner).
5. The owner's and the hunter's USDC go back to the relayer, and the owner account is deleted
   with the relayer as beneficiary, so all money ends with the relayer.

Without --i-approve-mainnet only read-only RPC calls are made and the plan is printed:

    uv run python scripts/mainnet_escrow_hunt.py --api http://127.0.0.1:8650
    uv run python scripts/mainnet_escrow_hunt.py --api http://127.0.0.1:8650 --i-approve-mainnet

After a failed run, --sweep-only --i-approve-mainnet sends the owner's and the hunter's USDC
back to the relayer and deletes the owner account.
"""

import argparse
import json
import os
import struct
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from app.core.config import settings
from app.settlement import escrow
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    OutcomeUnknown,
    generate_key,
    implicit_account_id,
    load_signing_key,
    near_balance,
    public_key_text,
    transfer_action,
    upfront_gas_cost,
    view_account,
)

ESCROW = "escrow.arsune.near"
CODE_HASH = "GSF2obxd3HuyheiNTE69wB3yDTh7Z9vREmDFi24Yr14s"
# Hard caps, in USDC's smallest unit (6 decimals) and yoctoNEAR.
REWARD = 100_000  # 0.10 USDC
BOND = 10_000  # 0.01 USDC base bond; a new hunter pays exactly this
FEE_BPS = 1_000  # 0.01 USDC fee to Arsune on the proven claim
OWNER_USDC = 300_000  # the owner's budget
HUNTER_USDC = 50_000  # the hunter's bonds
USDC_CAP = 1_000_000
CLAIMS = 20  # ARSUNE_ESCROW_CLAIMS of the stack
YOCTO = 10**24
OWNER_NEAR = 3 * 10**22  # 0.03 NEAR: account storage and close_bounty gas
GAS_RESERVE = 25 * 10**22  # prepaid gas the relayer must hold (rule alone holds 0.11 NEAR)
NEAR_CAP = 5 * 10**23  # the run may need at most 0.5 NEAR on the relayer
ACCOUNTS = Path(__file__).resolve().parent.parent / ".data" / "mainnet-escrow-hunt.json"
SITE = "http://demo:8541"  # the demo site as the API containers reach it
EXPLORER = "https://nearblocks.io/txns/{}"
POLL_SECONDS = 2
assert OWNER_USDC + HUNTER_USDC <= USDC_CAP
assert OWNER_USDC >= 2 * (REWARD + REWARD * FEE_BPS // 10_000)


def refuse(reason: str) -> int:
    print(f"REFUSED: {reason}")
    return 2


def usdc(units: int) -> str:
    return f"{units / 10**6:.6f} USDC"


def near(yocto: int) -> str:
    return f"{yocto / YOCTO:.6f} NEAR"


def make_api(url: str) -> httpx.Client:
    return httpx.Client(base_url=url, timeout=120, trust_env=False)


def load_accounts(path: Path, create: bool) -> dict:
    """Owner and hunter keys. A new file is written only in approve mode."""
    if path.exists():
        return json.loads(path.read_text())
    people = {}
    for role in ("owner", "hunter"):
        private, _ = generate_key()
        people[role] = {
            "account_id": implicit_account_id(load_signing_key(private)),
            "private_key": private,
        }
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            json.dump(people, handle, indent=2)
    return people


def delete_account_action(beneficiary: str) -> bytes:
    data = beneficiary.encode()
    return b"\x07" + struct.pack("<I", len(data)) + data


class Hunt:
    def __init__(self, rpc: NearRpc, people: dict, api: httpx.Client | None = None):
        self.rpc, self.people, self.api = rpc, people, api
        self.relayer = settings.near_account_id
        self.key = load_signing_key(settings.near_private_key)
        self.token, self.intents = settings.bounty_token, settings.intents_contract
        self.escrow = escrow.EscrowClient(
            ESCROW, rpc=rpc, intents_contract=self.intents, token_id=self.token
        )
        self.txs: list[tuple[str, str]] = []
        self.salt: bytes | None = None

    def usdc_of(self, account: str) -> int:
        return ni.balance(account, self.token, rpc=self.rpc, contract=self.intents)

    def balances(self) -> dict:
        rows = {"relayer": self.relayer, **{r: p["account_id"] for r, p in self.people.items()}}
        found = {role: self.usdc_of(account) for role, account in rows.items()}
        return found | {"escrow": self.usdc_of(ESCROW)}

    def note(self, what: str, tx_hash: str) -> None:
        self.txs.append((what, tx_hash))
        print(f"  {what}: {EXPLORER.format(tx_hash)}")

    def deadline(self) -> datetime:
        return datetime.now(UTC) + timedelta(minutes=5)

    def current_salt(self) -> bytes:
        if self.salt is None:
            self.salt = ni.current_salt(rpc=self.rpc, contract=self.intents)
        return self.salt

    def transfer(self, sender: dict, receiver: str, amount: int, memo: str) -> dict:
        return ni.sign_transfer(
            sender["account_id"],
            sender["private_key"],
            receiver,
            self.token,
            amount,
            memo,
            self.deadline(),
            salt=self.current_salt(),
            contract=self.intents,
        )

    def execute(self, what: str, signed: list[dict]) -> None:
        tx_hash = ni.execute(
            signed,
            account_id=self.relayer,
            private_key=settings.near_private_key,
            rpc=self.rpc,
            contract=self.intents,
            gas=50 * TGAS,
        )
        self.note(what, tx_hash)

    def fund_people(self) -> None:
        owner, hunter = self.people["owner"], self.people["hunter"]
        if near_balance(self.rpc, owner["account_id"]) < OWNER_NEAR // 2:
            tx_hash, _ = self.rpc.send_actions(
                self.relayer, self.key, owner["account_id"], [transfer_action(OWNER_NEAR)]
            )
            self.note(f"relayer -> owner {near(OWNER_NEAR)}", tx_hash)
        relayer = {"account_id": self.relayer, "private_key": settings.near_private_key}
        signed = []
        for person, amount in ((owner, OWNER_USDC), (hunter, HUNTER_USDC)):
            missing = amount - self.usdc_of(person["account_id"])
            if missing > 0:
                memo = f"arsune:hunt-fund:{person['account_id'][:8]}"
                signed.append(self.transfer(relayer, person["account_id"], missing, memo))
        if signed:
            self.execute("relayer -> owner and hunter USDC in intents.near", signed)

    def post(self, path: str, body: dict | None = None) -> dict:
        response = self.api.post(path, json=body)
        if response.status_code >= 300:
            raise SettlementError(f"POST {path}: HTTP {response.status_code} {response.text}")
        return response.json()

    def get(self, path: str, **params) -> dict:
        response = self.api.get(path, params=params or None)
        if response.status_code >= 300:
            raise SettlementError(f"GET {path}: HTTP {response.status_code} {response.text}")
        return response.json()

    def deposit(self, person: dict, amount: int, msg: str, memo: str) -> dict:
        return escrow.sign_deposit(
            person["account_id"],
            person["private_key"],
            ESCROW,
            self.token,
            amount,
            msg,
            memo,
            self.deadline(),
            salt=self.current_salt(),
            contract=self.intents,
        )

    def check_terms(self, bounty_id: str) -> dict:
        view = self.escrow.get_bounty(bounty_id)
        expected = {
            "owner": self.people["owner"]["account_id"],
            "judge": self.relayer,
            "fee_account": self.relayer,
            "reward": str(REWARD),
            "base_bond": str(BOND),
            "fee_bps": FEE_BPS,
        }
        if view is None or any(view.get(k) != v for k, v in expected.items()):
            raise SettlementError(f"escrow terms differ from the plan: {view}")
        print(f"  escrow terms checked; claims_left {view.get('claims_left')} (planned {CLAIMS})")
        return view

    def open_and_fund(self) -> str:
        owner = self.people["owner"]
        body = {"url": SITE + "/faulty/", "owner_account": owner["account_id"]}
        body |= {"reward_amount": REWARD, "bond_amount": BOND, "fee_bps": FEE_BPS}
        bounty_id = self.post("/api/bounties", body | {"budget": OWNER_USDC})["id"]
        print(f"bounty {bounty_id}")
        self.post(f"/api/bounties/{bounty_id}/demo-proof")
        self.post(f"/api/bounties/{bounty_id}/verify")
        view = self.wait(
            lambda: self.get(f"/api/bounties/{bounty_id}"),
            lambda v: v["status"] == "open" or (v["site_check"] or {}).get("state") == "failed",
            300,
            "the site check",
        )
        if view["status"] != "open":
            raise SettlementError(f"the bounty did not open: {view['site_check']}")
        self.check_terms(bounty_id)
        fund_id = str(uuid4())
        msg, memo = escrow.fund_msg(bounty_id), f"arsune:fund:{fund_id}"
        signed = self.deposit(owner, OWNER_USDC, msg, memo)
        body = {"fund_id": fund_id, "sender": owner["account_id"], "amount": OWNER_USDC}
        funded = self.post(f"/api/bounties/{bounty_id}/fund", body | {"signed_intent": signed})
        print(f"  funded: budget {usdc(funded['budget_amount'])}")
        return bounty_id

    def claim(self, bounty_id: str, target: str, expected: str) -> dict:
        hunter, claim_id = self.people["hunter"], str(uuid4())
        quote = self.get(f"/api/bounties/{bounty_id}/bond", hunter=hunter["account_id"])
        bond = quote["required_bond"]
        if not 0 < bond <= 3 * BOND:
            raise SettlementError(f"bond {bond} is outside the plan")
        source, dest = SITE + "/faulty/", f"{SITE}/faulty/{target}"
        body = {
            "claim_id": claim_id,
            "hunter_account": hunter["account_id"],
            "claim_type": "broken_link",
            "urls": [source, dest],
            "details": {"source": source, "target": dest},
            "explanation": f"The link to /faulty/{target} is broken.",
            "reproduction_steps": ["Open /faulty/.", f"Follow the link to /faulty/{target}."],
            "bond_intent": self.deposit(
                hunter, bond, escrow.bond_msg(bounty_id, claim_id), f"arsune:bond:{claim_id}"
            ),
        }
        self.post(f"/api/bounties/{bounty_id}/claims", body)
        view = self.wait(
            lambda: self.get(f"/api/claims/{claim_id}"),
            lambda v: v["settled_at"] or v["status"] == "needs_review",
            600,
            f"claim {claim_id}",
        )
        record = self.escrow.get_claim(claim_id) or {}
        same = record.get("evidence_hash") == view["evidence_hash"]
        print(
            f"  claim /faulty/{target}: API {view['status']}/{view['outcome']}, escrow "
            f"{record.get('outcome')}, evidence hash matches: {same} (expected {expected})"
        )
        if view["status"] == "needs_review" or record.get("outcome") is None:
            raise SettlementError(f"claim {claim_id} is not ruled on the escrow: {view}")
        return view

    def close_and_withdraw(self, bounty_id: str) -> None:
        owner = self.people["owner"]
        call = self.escrow.close_bounty(
            owner["account_id"], load_signing_key(owner["private_key"]), bounty_id
        )
        self.note("owner close_bounty", call.tx_hash)
        closed = self.post(f"/api/bounties/{bounty_id}/withdraw")
        print(f"  withdrawn: {closed['status']} ({closed['closed_reason']})")

    def sweep(self) -> None:
        """Send the owner's and the hunter's USDC to the relayer; delete the owner account."""
        signed = []
        for role, person in self.people.items():
            amount = self.usdc_of(person["account_id"])
            if amount > 0:
                memo = f"arsune:hunt-return:{role}"
                signed.append(self.transfer(person, self.relayer, amount, memo))
                print(f"  return {usdc(amount)} from the {role}")
        if signed:
            self.execute("owner and hunter USDC -> relayer", signed)
        owner = self.people["owner"]
        if view_account(self.rpc, owner["account_id"]) is not None:
            tx_hash, _ = self.rpc.send_actions(
                owner["account_id"],
                load_signing_key(owner["private_key"]),
                owner["account_id"],
                [delete_account_action(self.relayer)],
            )
            self.note("delete owner account, NEAR -> relayer", tx_hash)

    def links(self, bounty_id: str | None) -> None:
        print("Transactions (nearblocks):")
        for what, tx_hash in self.txs:
            print(f"  {what:<48} {EXPLORER.format(tx_hash)}")
        if bounty_id and self.api is not None:
            try:
                ledger = self.get(f"/api/bounties/{bounty_id}")["ledger"]
            except (SettlementError, httpx.HTTPError) as error:
                print(f"  (the API ledger is not available: {error})")
                ledger = []
            for row in ledger:
                if row.get("explorer_url"):
                    what = f"{row['kind']} {usdc(row['amount'])}"
                    print(f"  {what:<48} {row['explorer_url']}")

    @staticmethod
    def wait(read, done, seconds: int, what: str) -> dict:
        give_up = time.monotonic() + seconds
        while True:
            view = read()
            if done(view):
                return view
            if time.monotonic() > give_up:
                last = view.get("site_check") or view.get("status")
                raise SettlementError(f"timed out waiting for {what}: {last}")
            time.sleep(POLL_SECONDS)


def check_api(api: httpx.Client, relayer: str) -> str | None:
    """None when the API runs in escrow mode on this escrow with this relayer; else why not."""
    try:
        info = api.get("/api/settlement").json()
    except (httpx.HTTPError, ValueError) as error:
        return f"the Arsune API is not reachable: {error}"
    if info.get("mode") != "escrow" or info.get("escrow_contract") != ESCROW:
        return f"the API must run in escrow mode on {ESCROW}: {info.get('mode')}"
    if info.get("arsune_account") != relayer:
        return "the API uses another Arsune account than ARSUNE_NEAR_ACCOUNT_ID"
    return None


def print_balances(title: str, hunt: Hunt, relayer_near: int) -> dict:
    found = hunt.balances()
    print(title)
    print(f"  relayer NEAR        {near(relayer_near)}")
    for role, amount in found.items():
        print(f"  {role:<8} USDC in intents.near  {usdc(amount)}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://127.0.0.1:8650", help="Arsune API (escrow mode)")
    parser.add_argument("--accounts", type=Path, default=ACCOUNTS)
    parser.add_argument("--sweep-only", action="store_true")
    parser.add_argument("--i-approve-mainnet", action="store_true")
    args = parser.parse_args(argv)

    relayer = settings.near_account_id
    if not relayer or not settings.near_private_key.get_secret_value():
        return refuse("set ARSUNE_NEAR_ACCOUNT_ID and ARSUNE_NEAR_PRIVATE_KEY")
    try:
        key = load_signing_key(settings.near_private_key)
    except SettlementError as error:
        return refuse(str(error))
    if implicit_account_id(key) != relayer and len(relayer) == 64:
        return refuse("ARSUNE_NEAR_PRIVATE_KEY does not belong to ARSUNE_NEAR_ACCOUNT_ID")

    rpc = NearRpc(settings.near_rpc_url, timeout=60)
    people = load_accounts(args.accounts, create=args.i_approve_mainnet)
    hunt = Hunt(rpc, people)
    gas_price = int(rpc.call("gas_price", [None])["gas_price"])
    relayer_near = near_balance(rpc, relayer)
    account = view_account(rpc, ESCROW) or {}
    config = hunt.escrow.get_config()
    storage = escrow.storage_deposit(CLAIMS)
    owner_pk = public_key_text(load_signing_key(people["owner"]["private_key"]))
    implicit_ok = ni.has_public_key(
        people["owner"]["account_id"], owner_pk, rpc=rpc, contract=hunt.intents
    )
    need_near = storage + OWNER_NEAR + GAS_RESERVE
    rule_hold = upfront_gas_cost(escrow.RULE_GAS, gas_price)

    print("Planned escrow hunt on NEAR mainnet")
    print(f"  rpc                {settings.near_rpc_url}")
    print(f"  escrow             {ESCROW} code_hash {account.get('code_hash')}")
    print(f"  escrow config      intents {config['intents_contract']}, token {config['token_id']}")
    print(f"  escrow liabilities {usdc(int(config['liabilities']))}")
    print(f"  relayer (judge)    {relayer}")
    print(f"  owner (implicit)   {people['owner']['account_id']}")
    print(f"  hunter (implicit)  {people['hunter']['account_id']}")
    print(f"  keys file          {args.accounts} ({'exists' if args.accounts.exists() else 'new'})")
    print(f"  implicit key valid on intents.near without registration: {implicit_ok}")
    print(f"  reward {usdc(REWARD)}, base bond {usdc(BOND)}, fee {FEE_BPS} bps")
    print(f"  owner funding {usdc(OWNER_USDC)}, hunter gets {usdc(HUNTER_USDC)}")
    print(f"  USDC at risk       {usdc(OWNER_USDC + HUNTER_USDC)} (cap {usdc(USDC_CAP)})")
    print(f"  storage for {CLAIMS} claims  {near(storage)} (unused part back at withdraw)")
    print(f"  NEAR to the owner  {near(OWNER_NEAR)} (back when the account is deleted)")
    print(f"  gas price {gas_price} yocto; rule holds {near(rule_hold)} of prepaid gas")
    print(f"  relayer must hold  {near(need_near)} (cap {near(NEAR_CAP)})")
    print("  expected end: proven claim pays 0.10 + 0.01 fee, false bond 0.01 to the owner;")
    print("  all USDC returns to the relayer; NEAR spent: claim and bounty records + gas")
    before = print_balances("Balances now", hunt, relayer_near)

    if account.get("code_hash") != CODE_HASH:
        return refuse(f"{ESCROW} code hash is not {CODE_HASH}")
    if config["intents_contract"] != hunt.intents or config["token_id"] != hunt.token:
        return refuse("the escrow uses another intents contract or token than the settings")
    if not implicit_ok:
        return refuse("intents.near does not accept the implicit owner key")
    if need_near > NEAR_CAP:
        return refuse(f"the run needs more than {near(NEAR_CAP)}")
    if relayer_near < need_near:
        return refuse(f"the relayer needs {near(need_near)} for storage, owner NEAR and gas")
    if not args.sweep_only:
        plan = (("owner", OWNER_USDC), ("hunter", HUNTER_USDC))
        missing = sum(max(0, amount - before[role]) for role, amount in plan)
        if before["relayer"] < missing:
            return refuse(f"the relayer needs {usdc(missing)} in intents.near")
    api = make_api(args.api)
    problem = None if args.sweep_only else check_api(api, relayer)
    print(f"  API {args.api}: {problem or 'escrow mode, same escrow and relayer'}")

    if not args.i_approve_mainnet:
        print("Not sent. Start the escrow stack, then re-run with --i-approve-mainnet.")
        return 1
    if problem:
        return refuse(problem)

    hunt.api, bounty_id = api, None
    try:
        if not args.sweep_only:
            print("Funding the owner and the hunter")
            hunt.fund_people()
            bounty_id = hunt.open_and_fund()
            hunt.claim(bounty_id, "missing", "proven")
            hunt.claim(bounty_id, "healthy", "rejected")
            liabilities = int(hunt.escrow.get_config()["liabilities"])
            print(f"  escrow liabilities before withdraw {usdc(liabilities)}")
            hunt.close_and_withdraw(bounty_id)
        print("Returning money to the relayer")
        hunt.sweep()
    except OutcomeUnknown as error:
        print(f"UNKNOWN: {error}. Check {EXPLORER.format(error.tx_hash)} before retrying.")
        hunt.links(bounty_id)
        return 3
    except SettlementError as error:
        print(f"FAILED: {error}")
        print("Keys stay in the keys file. Re-run with --sweep-only --i-approve-mainnet to")
        print("return the owner's and the hunter's USDC to the relayer.")
        hunt.links(bounty_id)
        return 3
    hunt.links(bounty_id)
    after = print_balances("Balances after", hunt, near_balance(rpc, relayer))
    print("USDC change inside intents.near:")
    for role, amount in before.items():
        print(f"  {role:<8} {(after[role] - amount) / 10**6:+.6f} USDC")
    print(f"  relayer NEAR change {near(near_balance(rpc, relayer) - relayer_near)}")
    print("DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
