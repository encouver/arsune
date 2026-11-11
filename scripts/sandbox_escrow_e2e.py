"""Escrow mode end to end on a local NEAR sandbox node. Sandbox only: never mainnet.

1. Start a sandbox node. `contracts/escrow/test.sh` downloads the nearcore sandbox binary into
   its cache: `$ESCROW_CACHE_DIR/sandbox/debug/build/near-sandbox-*/out/.near/near-sandbox-*/`.
       near-sandbox --home /tmp/sb init
       near-sandbox --home /tmp/sb run --rpc-addr 127.0.0.1:8830 --network-addr 127.0.0.1:8831
2. Deploy test USDC, the real intents.near wasm and the escrow (fixtures from test.sh), and
   fund an owner and three hunters. It prints the ARSUNE_* settings for step 3.
       uv run python scripts/sandbox_escrow_e2e.py setup http://127.0.0.1:8830 /tmp/sb accounts.json
3. Run the API, coordinator and worker with those settings, and the demo site.
4. Open, fund, and claim (a proven claim with a scout, a false claim, a duplicate); the owner
   closes the bounty and Arsune withdraws the rest to the owner.
       uv run python scripts/sandbox_escrow_e2e.py run <api_url> <demo_site_url> accounts.json
"""

import json
import struct
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
from app.settlement import escrow
from app.settlement.near_tx import (
    TGAS,
    NearRpc,
    add_full_access_key_action,
    create_account_action,
    decode_ed25519,
    function_call_action,
    generate_key,
    implicit_account_id,
    load_signing_key,
    transfer_action,
)

NEAR = 10**24
ESCROW_DIR = Path(__file__).resolve().parent.parent / "contracts" / "escrow" / "target"
REWARD, BOND, BUDGET = 1_000_000, 100_000, 5_000_000
PEOPLE = {"owner": 20_000_000, "hunter-a": 2_000_000, "hunter-b": 2_000_000, "hunter-c": 2_000_000}


def deploy_action(code: bytes) -> bytes:
    return b"\x01" + struct.pack("<I", len(code)) + code


class Sandbox:
    def __init__(self, rpc_url: str, home: str):
        validator = json.loads(Path(home, "validator_key.json").read_text())
        if "mainnet" in rpc_url or validator["account_id"] != "test.near":
            raise SystemExit("This script runs only against a local sandbox node.")
        self.rpc = NearRpc(rpc_url, timeout=60)
        self.root, self.key = validator["account_id"], load_signing_key(validator["secret_key"])

    def create(self, name, near, *, code=None, init=None, keyed=True):
        """A sub-account of the root; without a key it is locked, like the mainnet escrow."""
        account, private = f"{name}.{self.root}", None
        actions = [create_account_action(), transfer_action(near * NEAR)]
        if keyed:
            private, public = generate_key()
            actions.append(add_full_access_key_action(decode_ed25519(public, 32, "public key")))
        if code:
            actions.append(deploy_action(code))
        if init:
            method, args = init
            actions.append(function_call_action(method, json.dumps(args).encode(), 150 * TGAS, 0))
        self.rpc.send_actions(self.root, self.key, account, actions)
        return account, private

    def call(self, receiver, method, args, *, signer=None, key=None, gas=100 * TGAS, deposit=0):
        signer, key = signer or self.root, key or self.key
        self.rpc.call_function(signer, key, receiver, method, args, gas=gas, deposit=deposit)


def setup(rpc_url: str, home: str, out: str) -> None:
    chain = Sandbox(rpc_url, home)
    fixtures = ESCROW_DIR / "fixtures"
    usdc, _ = chain.create(
        "usdc",
        10,
        code=(fixtures / "fungible-token.wasm").read_bytes(),
        init=("new_default_meta", {"owner_id": chain.root, "total_supply": str(10**15)}),
        keyed=False,
    )
    intents, intents_key = chain.create(
        "intents", 50, code=(fixtures / "defuse-0.4.5.wasm").read_bytes()
    )
    roles = {"super_admins": [chain.root], "admins": {}, "grantees": {"DAO": [chain.root]}}
    fees = {"fee": 0, "fee_collector": chain.root}
    config = {"wnear_id": f"wrap.{chain.root}", "fees": fees, "roles": roles}
    key = load_signing_key(intents_key)
    chain.call(intents, "new", {"config": config}, signer=intents, key=key, gas=300 * TGAS)
    chain.call(usdc, "storage_deposit", {"account_id": intents}, deposit=10**22)
    token = f"nep141:{usdc}"
    contract, _ = chain.create(
        "escrow",
        5,
        code=(ESCROW_DIR / "near" / "arsune_escrow.wasm").read_bytes(),
        init=("new", {"intents_contract": intents, "token_id": token}),
        keyed=False,
    )
    arsune, arsune_key = chain.create("arsune", 30)
    people = {}
    for role, amount in {**PEOPLE, "scout": 0}.items():
        private, _ = generate_key()
        account = implicit_account_id(load_signing_key(private))
        if amount:
            args = {"receiver_id": intents, "amount": str(amount), "msg": account}
            chain.call(usdc, "ft_transfer_call", args, deposit=1)
        people[role] = {"account_id": account, "private_key": private}
    # The owner signs close_bounty itself, so its implicit account needs NEAR.
    chain.rpc.send_actions(
        chain.root, chain.key, people["owner"]["account_id"], [transfer_action(NEAR)]
    )
    accounts = {"rpc_url": rpc_url, "token": token, "intents": intents, "escrow": contract}
    accounts |= {"arsune": {"account_id": arsune, "private_key": arsune_key}, **people}
    Path(out).write_text(json.dumps(accounts, indent=2))
    print(f"Wrote {out}. Settings for the API, coordinator and worker (sandbox keys only):")
    for name, value in [
        ("SETTLEMENT_MODE", "escrow"),
        ("NEAR_RPC_URL", rpc_url),
        ("INTENTS_CONTRACT", intents),
        ("BOUNTY_TOKEN", token),
        ("ESCROW_CONTRACT", contract),
        ("NEAR_ACCOUNT_ID", arsune),
        ("NEAR_PRIVATE_KEY", arsune_key),
    ]:
        print(f"export ARSUNE_{name}={value}")


class Run:
    def __init__(self, api_url: str, site: str, accounts: str):
        self.a = json.loads(Path(accounts).read_text())
        self.api = httpx.Client(base_url=api_url, timeout=120)
        self.site = site
        self.info = self.api.get("/api/settlement").json()
        self.rpc = NearRpc(self.a["rpc_url"], timeout=60)
        self.escrow = escrow.EscrowClient(
            self.a["escrow"],
            rpc=self.rpc,
            intents_contract=self.a["intents"],
            token_id=self.a["token"],
        )

    def usdc(self, account: str) -> int:
        args = {"account_id": account, "token_id": self.a["token"]}
        return int(self.rpc.view(self.a["intents"], "mt_balance_of", args))

    def balances(self) -> dict:
        roles = [*PEOPLE, "scout", "arsune"]
        found = {role: self.usdc(self.a[role]["account_id"]) for role in roles}
        return found | {"escrow": self.usdc(self.a["escrow"])}

    def deposit(self, person: dict, amount: int, msg: str, memo: str) -> dict:
        deadline = datetime.now(UTC) + timedelta(minutes=5)
        return escrow.sign_deposit(
            person["account_id"],
            person["private_key"],
            self.info["escrow_contract"],
            self.info["token"],
            amount,
            msg,
            memo,
            deadline,
            salt=bytes.fromhex(self.info["salt"]),
            contract=self.info["intents_contract"],
        )

    def post(self, path: str, body: dict | None = None) -> dict:
        response = self.api.post(path, json=body)
        if response.status_code >= 300:
            raise SystemExit(f"POST {path}: HTTP {response.status_code} {response.text}")
        return response.json()

    def open_bounty(self) -> dict:
        owner = self.a["owner"]
        body = {"url": self.site + "/faulty/", "owner_account": owner["account_id"]}
        body |= {"reward_amount": REWARD, "bond_amount": BOND, "budget": BUDGET}
        bounty = self.post("/api/bounties", body)
        httpx.post(self.site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
        self.post(f"/api/bounties/{bounty['id']}/verify")
        # The worker checks the site, then opens the bounty on the escrow.
        for _ in range(120):
            view = self.api.get(f"/api/bounties/{bounty['id']}").json()
            if view["status"] == "open" or view["site_check"]["state"] != "checking":
                break
            time.sleep(1)
        if view["status"] != "open":
            raise SystemExit(f"The bounty did not open: {view['site_check']}")
        fund_id = str(uuid4())
        msg, memo = escrow.fund_msg(bounty["id"]), f"arsune:fund:{fund_id}"
        signed = self.deposit(owner, BUDGET, msg, memo)
        body = {"fund_id": fund_id, "sender": owner["account_id"], "amount": BUDGET}
        return self.post(f"/api/bounties/{bounty['id']}/fund", body | {"signed_intent": signed})

    def claim(self, bounty: dict, role: str, target: str, scout: dict | None) -> dict:
        hunter, claim_id = self.a[role], str(uuid4())
        params = {"hunter": hunter["account_id"]}
        bond = self.api.get(f"/api/bounties/{bounty['id']}/bond", params=params).json()
        share = (scout["account"], scout["share_bps"]) if scout else None
        msg = escrow.bond_msg(bounty["id"], claim_id, share)
        source, dest = self.site + "/faulty/", f"{self.site}/faulty/{target}"
        body = {
            "claim_id": claim_id,
            "hunter_account": hunter["account_id"],
            "claim_type": "broken_link",
            "urls": [source, dest],
            "details": {"source": source, "target": dest},
            "explanation": f"The link to /faulty/{target} is broken.",
            "reproduction_steps": ["Open /faulty/.", f"Follow the link to /faulty/{target}."],
            "bond_intent": self.deposit(
                hunter, bond["required_bond"], msg, f"arsune:bond:{claim_id}"
            ),
        }
        if scout:
            body["scout"] = scout
        self.post(f"/api/bounties/{bounty['id']}/claims", body)
        for _ in range(180):
            view = self.api.get(f"/api/claims/{claim_id}").json()
            if view["settled_at"] or view["status"] == "needs_review":
                return view
            time.sleep(1)
        raise SystemExit(f"claim {claim_id} did not settle")

    def close_and_withdraw(self, bounty: dict) -> dict:
        owner = self.a["owner"]
        key = load_signing_key(owner["private_key"])
        self.escrow.close_bounty(owner["account_id"], key, bounty["id"])
        return self.post(f"/api/bounties/{bounty['id']}/withdraw")


def run(api_url: str, site: str, accounts: str) -> None:
    e2e = Run(api_url, site, accounts)
    before = e2e.balances()
    bounty = e2e.open_bounty()
    print(f"bounty {bounty['id']} funded: budget {bounty['budget_amount']}")
    scout = {"account": e2e.a["scout"]["account_id"], "share_bps": 2500, "tip_id": "tip-1"}
    plan = [("hunter-a", "missing", scout), ("hunter-b", "healthy", None)]
    plan.append(("hunter-c", "missing", None))
    for role, target, tip in plan:
        view = e2e.claim(bounty, role, target, tip)
        record = e2e.escrow.get_claim(view["id"])
        same = record["evidence_hash"] == view["evidence_hash"]
        print(
            f"{role}: claim {view['id']} {view['status']}; escrow {record['outcome']}, hash {same}"
        )
    after = e2e.balances()
    print("USDC change inside intents.near (smallest unit):")
    for name, amount in before.items():
        print(f"  {name:<9} {after[name] - amount:+d}")
    liabilities = e2e.escrow.get_config()["liabilities"]
    print(f"escrow liabilities {liabilities}, escrow balance {after['escrow']}")
    closed = e2e.close_and_withdraw(bounty)
    print(
        f"withdrawn: {closed['status']} ({closed['closed_reason']}); escrow balance {e2e.usdc(e2e.a['escrow'])}"
    )


if __name__ == "__main__":
    commands = {"setup": setup, "run": run}
    if len(sys.argv) != 5 or sys.argv[1] not in commands:
        raise SystemExit(__doc__)
    commands[sys.argv[1]](*sys.argv[2:])
