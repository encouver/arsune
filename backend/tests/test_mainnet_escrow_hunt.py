"""Offline checks of scripts/mainnet_escrow_hunt.py. A fake chain and a fake Arsune API stand
in for mainnet and the escrow-mode stack; nothing opens a socket."""

import importlib.util
import json
import re
import struct
from pathlib import Path

import httpx
import pytest
from app.core.config import Settings
from app.settlement import escrow
from app.settlement import near_intents as ni
from app.settlement.near_tx import (
    NearRpcError,
    decode_ed25519,
    generate_key,
    implicit_account_id,
    load_signing_key,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mainnet_escrow_hunt.py"
TOKEN = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
PRIVATE_KEY, _ = generate_key()
RELAYER = implicit_account_id(load_signing_key(PRIVATE_KEY))
NEAR = 10**24


def load_script():
    spec = importlib.util.spec_from_file_location("mainnet_escrow_hunt", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Chain:
    """intents.near balances, NEAR balances and the escrow's books, as the fakes share them."""

    def __init__(self, escrow_id):
        self.escrow = escrow_id
        self.usdc = {RELAYER: 1_000_000}
        self.near = {RELAYER: 11 * NEAR // 10}
        self.code_hash = "GSF2obxd3HuyheiNTE69wB3yDTh7Z9vREmDFi24Yr14s"
        self.bounties, self.claims, self.closed = {}, {}, set()
        self.sent, self.count = [], 0

    def move(self, sender, receiver, amount):
        assert 0 < amount <= self.usdc.get(sender, 0), (sender, amount)
        self.usdc[sender] -= amount
        self.usdc[receiver] = self.usdc.get(receiver, 0) + amount

    def tx(self):
        self.count += 1
        return f"Tx{self.count}"


class FakeRpc:
    def __init__(self, chain):
        self.chain = chain

    def call(self, method, params):
        if method == "gas_price":
            return {"gas_price": "100000000"}
        assert params["request_type"] == "view_account"
        account = params["account_id"]
        if account == self.chain.escrow:
            return {"amount": str(5 * NEAR), "code_hash": self.chain.code_hash}
        if account not in self.chain.near:
            raise NearRpcError({"cause": {"name": "UNKNOWN_ACCOUNT"}})
        return {"amount": str(self.chain.near[account]), "code_hash": "1" * 32}

    def view(self, contract, method, args):
        chain = self.chain
        if contract == "intents.near":
            answers = {
                "mt_balance_of": lambda: str(chain.usdc.get(args.get("account_id"), 0)),
                "current_salt": lambda: "00112233",
                "has_public_key": lambda: True,
            }
            return answers[method]()
        assert contract == chain.escrow
        if method == "get_config":
            return {
                "intents_contract": "intents.near",
                "token_id": TOKEN,
                "liabilities": str(chain.usdc.get(chain.escrow, 0)),
            }
        if method == "get_bounty":
            return chain.bounties.get(args["bounty_id"])
        return chain.claims.get(args["claim_id"])

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        chain = self.chain
        chain.sent.append((signer_id, receiver_id, method))
        assert implicit_account_id(key) == signer_id
        if method == "execute_intents":
            logs = []
            for signed in args["signed"]:
                message = ni._verify_nep413(signed, "intents.near")
                public = decode_ed25519(signed["public_key"], 32, "public key")
                assert public.hex() == message["signer_id"]  # implicit: the key is the account
                for intent in message["intents"]:
                    assert "msg" not in intent
                    (amount,) = intent["tokens"].values()
                    chain.move(message["signer_id"], intent["receiver_id"], int(amount))
                entry = {"account_id": message["signer_id"], "nonce": signed["payload"]["nonce"]}
                event = {"standard": "dip4", "event": "intents_executed", "data": [entry]}
                logs.append("EVENT_JSON:" + json.dumps(event))
            return chain.tx(), {"receipts_outcome": [{"outcome": {"logs": logs}}]}
        assert (receiver_id, method, deposit) == (chain.escrow, "close_bounty", 1)
        assert chain.bounties[args["bounty_id"]]["owner"] == signer_id
        chain.closed.add(args["bounty_id"])
        return chain.tx(), {"status": {"SuccessValue": ""}, "receipts_outcome": []}

    def send_actions(self, signer_id, key, receiver_id, actions):
        chain = self.chain
        chain.sent.append((signer_id, receiver_id, "actions"))
        assert implicit_account_id(key) == signer_id
        for action in actions:
            if action[0] == 3:  # Transfer
                amount = int.from_bytes(action[1:17], "little")
                chain.near[signer_id] -= amount
                chain.near[receiver_id] = chain.near.get(receiver_id, 0) + amount
            else:  # DeleteAccount
                assert action[0] == 7 and receiver_id == signer_id
                (size,) = struct.unpack("<I", action[1:5])
                beneficiary = action[5 : 5 + size].decode()
                chain.near[beneficiary] += chain.near.pop(signer_id)
        return chain.tx(), {}


def fake_api(chain, mode="escrow"):
    """The Arsune API in escrow mode: it relays the signed deposits and rules at once."""

    def deposit(signed, sender, amount, msg, memo):
        escrow.verify_deposit(
            signed,
            sender=sender,
            escrow=chain.escrow,
            token_id=TOKEN,
            amount=amount,
            msg=msg,
            memo=memo,
            contract="intents.near",
        )
        chain.move(sender, chain.escrow, amount)

    def handle(request):
        path, body = request.url.path, json.loads(request.content or b"null")
        if path == "/api/settlement":
            info = {"mode": mode, "escrow_contract": chain.escrow, "arsune_account": RELAYER}
            return httpx.Response(200, json=info)
        if path == "/api/bounties":
            bounty_id = f"0000000{len(chain.bounties)}-0000-4000-8000-000000000000"
            chain.bounties[bounty_id] = {
                "owner": body["owner_account"],
                "judge": RELAYER,
                "fee_account": RELAYER,
                "reward": str(body["reward_amount"]),
                "base_bond": str(body["bond_amount"]),
                "fee_bps": body["fee_bps"],
                "claims_left": "20",
                "balance": 0,
            }
            return httpx.Response(201, json={"id": bounty_id})
        if match := re.fullmatch(r"/api/bounties/([^/]+)(/[a-z-]+)?", path):
            bounty_id, action = match.groups()
            bounty = chain.bounties[bounty_id]
            if action in ("/demo-proof", "/verify"):
                return httpx.Response(200, json={})
            if action is None:
                ledger = [{"kind": "escrow_open", "amount": 0, "explorer_url": "https://x/Open"}]
                view = {"status": "open", "site_check": {"state": "passed"}, "ledger": ledger}
                return httpx.Response(200, json=view)
            if action == "/fund":
                memo = f"arsune:fund:{body['fund_id']}"
                msg = escrow.fund_msg(bounty_id)
                deposit(body["signed_intent"], body["sender"], body["amount"], msg, memo)
                bounty["balance"] += body["amount"]
                return httpx.Response(200, json={"budget_amount": bounty["balance"]})
            if action == "/bond":
                return httpx.Response(200, json={"required_bond": int(bounty["base_bond"])})
            if action == "/claims":
                return claim(bounty_id, bounty, body)
            assert action == "/withdraw" and bounty_id in chain.closed
            chain.move(chain.escrow, bounty["owner"], bounty["balance"])
            return httpx.Response(200, json={"status": "closed", "closed_reason": "withdrawn"})
        claim_id = path.removeprefix("/api/claims/")
        record = chain.claims[claim_id]
        view = {"status": record["outcome"], "outcome": record["outcome"], "settled_at": "now"}
        return httpx.Response(200, json=view | {"evidence_hash": record["evidence_hash"]})

    def claim(bounty_id, bounty, body):
        hunter, bond = body["hunter_account"], int(bounty["base_bond"])
        msg = escrow.bond_msg(bounty_id, body["claim_id"])
        deposit(body["bond_intent"], hunter, bond, msg, f"arsune:bond:{body['claim_id']}")
        if body["details"]["target"].endswith("/missing"):
            reward = int(bounty["reward"])
            fee = reward * bounty["fee_bps"] // 10_000
            chain.move(chain.escrow, hunter, reward + bond)
            chain.move(chain.escrow, RELAYER, fee)
            bounty["balance"] -= reward + fee
            outcome = "proven"
        else:
            bounty["balance"] += bond
            outcome = "rejected"
        chain.claims[body["claim_id"]] = {"outcome": outcome, "evidence_hash": "ab" * 32}
        return httpx.Response(202, json={})

    return httpx.MockTransport(handle)


@pytest.fixture
def run(monkeypatch, capsys, tmp_path):
    module = load_script()
    chain = Chain(module.ESCROW)
    config = Settings(
        _env_file=None,
        near_account_id=RELAYER,
        near_private_key=PRIVATE_KEY,
        near_rpc_url="http://127.0.0.1:9",
    )
    monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(module, "NearRpc", lambda url, timeout: FakeRpc(chain))
    keys = tmp_path / ".data" / "hunt.json"

    def main(*args, mode="escrow"):
        transport = fake_api(chain, mode)
        monkeypatch.setattr(
            module, "make_api", lambda url: httpx.Client(base_url=url, transport=transport)
        )
        code = module.main(["--accounts", str(keys), *args])
        output = capsys.readouterr().out
        assert PRIVATE_KEY[8:] not in output and "ed25519:" not in output
        return code, output

    main.chain, main.keys = chain, keys
    return main


def test_caps_keep_the_run_small():
    module = load_script()
    assert module.OWNER_USDC + module.HUNTER_USDC <= module.USDC_CAP == 1_000_000
    assert (module.REWARD, module.BOND, module.OWNER_USDC, module.HUNTER_USDC) == (
        100_000,
        10_000,
        300_000,
        50_000,
    )
    assert escrow.storage_deposit(module.CLAIMS) == 127 * 10**21  # 0.127 NEAR for 20 claims


def test_without_approval_only_the_plan_is_printed(run):
    code, output = run()
    assert code == 1 and "Not sent" in output
    assert "storage for 20 claims  0.127000 NEAR" in output
    assert "USDC at risk       0.350000 USDC" in output
    assert "relayer  USDC in intents.near  1.000000 USDC" in output
    assert "escrow mode, same escrow and relayer" in output
    assert run.chain.sent == [] and not run.keys.exists()


def test_approved_run_hunts_and_returns_every_unit_to_the_relayer(run):
    near_before = run.chain.near[RELAYER]
    code, output = run("--i-approve-mainnet")
    assert code == 0 and "DONE" in output, output
    chain = run.chain
    people = json.loads(run.keys.read_text())
    assert run.keys.stat().st_mode & 0o777 == 0o600
    owner, hunter = people["owner"]["account_id"], people["hunter"]["account_id"]
    # Every USDC unit is back with the relayer; the owner's NEAR came back on deletion.
    assert chain.usdc[RELAYER] == 1_000_000
    assert chain.usdc[owner] == chain.usdc[hunter] == chain.usdc[chain.escrow] == 0
    assert owner not in chain.near and chain.near[RELAYER] == near_before
    outcomes = sorted(claim["outcome"] for claim in chain.claims.values())
    assert outcomes == ["proven", "rejected"]
    assert "evidence hash matches: True (expected proven)" in output
    assert "https://nearblocks.io/txns/Tx" in output and "https://x/Open" in output
    assert (owner, chain.escrow, "close_bounty") in chain.sent


def test_sweep_only_returns_money_and_deletes_the_owner(run):
    run.keys.parent.mkdir(parents=True)
    people = {}
    for role in ("owner", "hunter"):
        private, _ = generate_key()
        people[role] = {
            "account_id": implicit_account_id(load_signing_key(private)),
            "private_key": private,
        }
    run.keys.write_text(json.dumps(people))
    owner, hunter = people["owner"]["account_id"], people["hunter"]["account_id"]
    run.chain.usdc |= {RELAYER: 650_000, owner: 200_000, hunter: 150_000}
    run.chain.near[owner] = 3 * 10**22
    code, output = run("--sweep-only", "--i-approve-mainnet", mode="dry")
    assert code == 0, output
    assert run.chain.usdc[RELAYER] == 1_000_000 and owner not in run.chain.near


def test_wrong_escrow_code_is_refused(run):
    run.chain.code_hash = "Other"
    code, output = run("--i-approve-mainnet")
    assert code == 2 and "code hash" in output
    assert run.chain.sent == []


def test_too_little_usdc_or_near_is_refused(run):
    run.chain.usdc[RELAYER] = 300_000
    code, output = run("--i-approve-mainnet")
    assert code == 2 and "0.350000 USDC in intents.near" in output
    run.chain.usdc[RELAYER], run.chain.near[RELAYER] = 1_000_000, NEAR // 10
    code, output = run("--i-approve-mainnet")
    assert code == 2 and "the relayer needs 0.407000 NEAR" in output
    assert run.chain.sent == []


def test_api_outside_escrow_mode_is_refused(run):
    code, output = run("--i-approve-mainnet", mode="near_intents")
    assert code == 2 and "escrow mode" in output
    assert run.chain.sent == []
