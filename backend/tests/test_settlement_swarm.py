"""Swarm accounts: transaction encoding vectors, the accounts file, and the setup script
against a fake chain. Nothing here talks to a real RPC."""

import base64
import hashlib
import importlib.util
import json
import stat
import struct
from pathlib import Path

import base58
import pytest
from app.core.config import Settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    NEP366_TAG,
    TGAS,
    NearRpc,
    NearRpcError,
    add_full_access_key_action,
    create_account_action,
    delegate_action,
    function_call_action,
    generate_key,
    implicit_account_id,
    load_signing_key,
    public_key_text,
    signed_delegate_action,
    transaction,
    transfer_action,
)
from app.settlement.swarm_accounts import (
    load_swarm_accounts,
    plan_swarm_accounts,
    save_swarm_accounts,
    swarm_roles,
)
from nacl.signing import VerifyKey

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "near_swarm_accounts.py"
TOKEN = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
RELAYER_KEY, RELAYER_PUBLIC = generate_key()
RELAYER = implicit_account_id(load_signing_key(RELAYER_KEY))
PARENT = "arsune-swarm.near"
ROLES = swarm_roles(6, 3)


# Transaction encoding vectors (near/near-api-js test/unit/transactions/serialize.test.ts) ---

MULTI_ACTION_TX = (
    "09000000746573742e6e656172000f56a5f028dfc089ec7c39c1183b321b4d8f89ba5bec9e1762803cc2491f6ef8"
    "0100000000000000030000003132330fa473fd26901df296be6adc4cc4df34d040efa2435224b6986910e630c2fef6"
    "08000000000103000000010203020300000071717103000000010203e80300000000000040420f0000000000000000"
    "0000000000037b0000000000000000000000000000000440420f00000000000000000000000000000f56a5f028dfc0"
    "89ec7c39c1183b321b4d8f89ba5bec9e1762803cc2491f6ef805000f56a5f028dfc089ec7c39c1183b321b4d8f89ba"
    "5bec9e1762803cc2491f6ef800000000000000000000030000007a7a7a010000000300000077777706000f56a5f028"
    "dfc089ec7c39c1183b321b4d8f89ba5bec9e1762803cc2491f6ef80703000000313233"
)
DELEGATE_VECTOR = bytes(
    [8, 16, 0, 0, 0, 116, 104, 101, 45, 117, 115, 101, 114, 46, 116, 101, 115, 116, 110, 101]
    + [116, 27, 0, 0, 0, 104, 101, 108, 108, 111, 46, 110, 101, 97, 114, 45, 101, 120, 97, 109]
    + [112, 108, 101, 115, 46, 116, 101, 115, 116, 110, 101, 116, 1, 0, 0, 0, 2, 12, 0, 0]
    + [0, 115, 101, 116, 95, 103, 114, 101, 101, 116, 105, 110, 103, 20, 0, 0, 0, 123, 34, 103]
    + [114, 101, 101, 116, 105, 110, 103, 34, 58, 34, 72, 101, 108, 108, 111, 34, 125, 0, 224, 87]
    + [235, 72, 27, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
    + [0, 3, 9, 120, 166, 131, 144, 0, 0, 61, 158, 123, 9, 0, 0, 0, 0, 0, 154, 156]
    + [80, 116, 108, 65, 42, 39, 47, 253, 146, 109, 67, 106, 83, 230, 57, 183, 195, 122, 150, 6]
    + [246, 220, 173, 35, 120, 139, 167, 94, 183, 29, 0, 41, 98, 10, 45, 51, 177, 89, 159, 190]
    + [247, 41, 255, 243, 17, 186, 140, 168, 139, 9, 81, 33, 8, 74, 73, 85, 254, 127, 62, 54]
    + [193, 60, 50, 235, 49, 13, 37, 152, 94, 172, 24, 198, 220, 119, 148, 99, 89, 19, 187, 251]
    + [80, 76, 230, 77, 28, 80, 140, 133, 81, 139, 159, 62, 245, 167, 4]
)


def test_actions_match_near_api_js_multi_action_vector():
    vector = bytes.fromhex(MULTI_ACTION_TX)
    public_key = bytes.fromhex("0f56a5f028dfc089ec7c39c1183b321b4d8f89ba5bec9e1762803cc2491f6ef8")
    block_hash = base58.b58decode("244ZQ9cgj3CQ6bWBdytfrJMuMQ1jdXLFGnr4HhvtCTnM")
    header = transaction("test.near", public_key, 1, "123", block_hash, [])[:-4]
    assert vector.startswith(header + struct.pack("<I", 8) + create_account_action())
    assert function_call_action("qqq", b"\x01\x02\x03", 1000, 1_000_000) in vector
    assert transfer_action(123) in vector
    # The vector adds a FunctionCall-permission key; FullAccess differs only in the last byte.
    function_call_key_prefix = add_full_access_key_action(public_key)[:-1] + b"\x00"
    assert function_call_key_prefix + b"\x00" + b"\x03\x00\x00\x00zzz" in vector
    assert add_full_access_key_action(public_key)[-1:] == b"\x01"


def test_delegate_action_matches_near_api_js_vector_and_signature():
    public_key = DELEGATE_VECTOR[-97:-65]
    body = delegate_action(
        "the-user.testnet",
        "hello.near-examples.testnet",
        [function_call_action("set_greeting", b'{"greeting":"Hello"}', 30 * TGAS, 0)],
        158_895_108_000_003,
        159_096_381,
        public_key,
    )
    assert DELEGATE_VECTOR[1:-65] == body
    signed_hash = hashlib.sha256(struct.pack("<I", NEP366_TAG) + body).digest()
    VerifyKey(public_key).verify(signed_hash, DELEGATE_VECTOR[-64:])

    key = load_signing_key(RELAYER_KEY)
    ours = signed_delegate_action(body, key)
    assert ours[0] == 8 and ours[1:-65] == body and ours[-65] == 0
    key.verify_key.verify(signed_hash, ours[-64:])


class ScriptedRpc(NearRpc):
    def __init__(self):
        super().__init__("http://127.0.0.1:9")
        self.sent = []

    def call(self, method, params):
        if method == "query":
            return {"nonce": 7, "block_height": 1_000, "block_hash": base58.b58encode(bytes(32))}
        if method == "send_tx":
            signed = base64.b64decode(params["signed_tx_base64"])
            self.sent.append(signed)
            tx_hash = base58.b58encode(hashlib.sha256(signed[:-65]).digest()).decode()
            return {
                "final_execution_status": "FINAL",
                "transaction": {"hash": tx_hash},
                "status": {"SuccessValue": ""},
                "transaction_outcome": {"outcome": {"status": {"SuccessReceiptId": "r"}}},
                "receipts_outcome": [],
            }
        raise AssertionError(method)


def test_relay_wraps_a_signed_delegate_in_the_relayer_transaction():
    rpc = ScriptedRpc()
    relayer = load_signing_key(RELAYER_KEY)
    sender = load_signing_key(generate_key()[0])
    actions = [ni.add_public_key_action(public_key_text(sender))]
    rpc.relay(RELAYER, relayer, "owner.arsune-swarm.near", sender, "intents.near", actions)
    (signed_tx,) = rpc.sent
    body = delegate_action(
        "owner.arsune-swarm.near", "intents.near", actions, 8, 1_100, bytes(sender.verify_key)
    )
    delegate = signed_delegate_action(body, sender)
    expected = transaction(
        RELAYER, bytes(relayer.verify_key), 8, "owner.arsune-swarm.near", bytes(32), [delegate]
    )
    assert signed_tx[:-65] == expected
    relayer.verify_key.verify(hashlib.sha256(expected).digest(), signed_tx[-64:])
    action = ni.add_public_key_action("ed25519:x")
    assert decode_function_call(action) == ("add_public_key", {"public_key": "ed25519:x"})
    assert action.endswith((10 * TGAS).to_bytes(8, "little") + (1).to_bytes(16, "little"))


# Accounts file ------------------------------------------------------------------------------


def test_accounts_file_round_trip_mode_and_resume(tmp_path):
    file = tmp_path / "nested" / "swarm-accounts.json"
    accounts = plan_swarm_accounts(PARENT, swarm_roles(2, 1))
    assert list(accounts) == ["parent", "owner", "hunter-1", "hunter-2", "scout-1"]
    assert accounts["hunter-2"].account_id == "hunter-2.arsune-swarm.near"
    assert accounts["owner"].private_key.get_secret_value() not in repr(accounts)
    save_swarm_accounts(accounts, file)
    assert stat.S_IMODE(file.stat().st_mode) == 0o600
    loaded = load_swarm_accounts(file)
    assert loaded == accounts

    smaller = plan_swarm_accounts(PARENT, swarm_roles(1, 0), loaded)
    assert set(smaller) == set(accounts) and smaller["owner"] == accounts["owner"]
    bigger = plan_swarm_accounts(PARENT, swarm_roles(3, 1), loaded)
    assert bigger["hunter-1"] == accounts["hunter-1"] and "hunter-3" in bigger
    with pytest.raises(SettlementError, match="not other.near"):
        plan_swarm_accounts("other.near", ROLES, loaded)
    with pytest.raises(SettlementError, match="cannot read"):
        load_swarm_accounts(tmp_path / "missing.json")


# Setup script against a fake chain ----------------------------------------------------------


def missing(cause):
    return NearRpcError({"name": "HANDLER_ERROR", "cause": {"name": cause}})


def decode_function_call(action: bytes) -> tuple[str, dict]:
    size = struct.unpack("<I", action[1:5])[0]
    method = action[5 : 5 + size].decode()
    args_size = struct.unpack("<I", action[5 + size : 9 + size])[0]
    return method, json.loads(action[9 + size : 9 + size + args_size])


class FakeChain:
    """The NearRpc surface the script uses, over an in-memory chain."""

    def __init__(self):
        self.accounts = {RELAYER: {"keys": {RELAYER_PUBLIC}, "near": 2 * 10**23}}
        self.registered = set()
        self.usdc = {RELAYER: 900_000}
        self.gas_price = 100_000_000
        self.sent = []
        self.fail_after = None

    def _record(self, entry):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise SettlementError("transaction X has a failed step")
        self.sent.append(entry)
        return f"Tx{len(self.sent)}"

    def call(self, method, params):
        if method == "gas_price":
            return {"gas_price": str(self.gas_price)}
        account = self.accounts.get(params["account_id"])
        if account is None:
            raise missing("UNKNOWN_ACCOUNT")
        if params["request_type"] == "view_account":
            return {"amount": str(account["near"])}
        if params["public_key"] not in account["keys"]:
            raise missing("UNKNOWN_ACCESS_KEY")
        return {"nonce": 1, "block_height": 10, "block_hash": "1" * 32}

    def view(self, contract, method, args):
        if method == "has_public_key":
            return (args["account_id"], args["public_key"]) in self.registered
        if method == "mt_balance_of":
            assert args["token_id"] == TOKEN
            return str(self.usdc.get(args["account_id"], 0))
        if method == "current_salt":
            return "252812b3"
        if method == "simulate_intents":
            return {"intents_executed": executed(args["signed"])}
        raise AssertionError(method)

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        assert signer_id == RELAYER and public_key_text(key) == RELAYER_PUBLIC
        if method == "create_account":
            assert (receiver_id, gas, deposit) == ("near", 50 * TGAS, 0)
            tx = self._record(("create_account", args["new_account_id"]))
            self.accounts[args["new_account_id"]] = {"keys": {args["new_public_key"]}, "near": 0}
            return tx, {"status": {"SuccessValue": base64.b64encode(b"true").decode()}}
        assert (receiver_id, method, gas, deposit) == (
            "intents.near",
            "execute_intents",
            50 * TGAS,
            0,
        )
        tx = self._record(("execute_intents", len(args["signed"])))
        for signed in args["signed"]:
            message = json.loads(signed["payload"]["message"])
            for intent in message["intents"]:
                assert list(intent["tokens"]) == [TOKEN]
                amount = int(intent["tokens"][TOKEN])
                receiver = intent["receiver_id"]
                self.usdc[message["signer_id"]] -= amount
                self.usdc[receiver] = self.usdc.get(receiver, 0) + amount
        event = {"standard": "dip4", "event": "intents_executed", "data": executed(args["signed"])}
        return tx, {
            "receipts_outcome": [{"outcome": {"logs": ["EVENT_JSON:" + json.dumps(event)]}}]
        }

    def relay(self, relayer_id, relayer_key, sender_id, sender_key, receiver_id, actions):
        assert relayer_id == RELAYER and public_key_text(relayer_key) == RELAYER_PUBLIC
        assert public_key_text(sender_key) in self.accounts[sender_id]["keys"]
        if actions[0] == create_account_action():
            assert receiver_id.endswith("." + sender_id) and receiver_id not in self.accounts
            tx = self._record(("create", receiver_id))
            key = "ed25519:" + base58.b58encode(actions[1][2:34]).decode()
            assert actions[1] == add_full_access_key_action(actions[1][2:34])
            self.accounts[receiver_id] = {"keys": {key}, "near": 0}
            return tx, {}
        method, args = decode_function_call(actions[0])
        assert (receiver_id, method) == ("intents.near", "add_public_key")
        assert actions[0] == ni.add_public_key_action(args["public_key"])
        tx = self._record(("register", sender_id))
        self.registered.add((sender_id, args["public_key"]))
        return tx, {}


def executed(signed_list):
    return [
        {
            "account_id": json.loads(s["payload"]["message"])["signer_id"],
            "nonce": s["payload"]["nonce"],
        }
        for s in signed_list
    ]


@pytest.fixture
def swarm(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location("near_swarm_accounts", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = Settings(
        _env_file=None,
        near_account_id=RELAYER,
        near_private_key=RELAYER_KEY,
        near_rpc_url="http://127.0.0.1:9",
        swarm_accounts_file=tmp_path / ".data" / "swarm-accounts.json",
    )
    monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(ni, "settings", config)
    chain = FakeChain()
    monkeypatch.setattr(module, "NearRpc", lambda url: chain)

    def run(*args):
        code = module.main(list(args))
        output = capsys.readouterr().out
        assert RELAYER_KEY[8:] not in output
        if config.swarm_accounts_file.exists():
            for account in load_swarm_accounts(config.swarm_accounts_file).values():
                assert account.private_key.get_secret_value()[8:] not in output
        return code, output

    run.chain, run.file, run.module = chain, config.swarm_accounts_file, module
    return run


def test_preview_prints_cost_table_and_sends_nothing(swarm):
    code, output = swarm()
    assert code == 1 and "Not sent" in output
    assert "create arsune-swarm.near via near.create_account" in output
    assert "create sub-account (relayed)" in output and " 10 " in output
    assert "execute_intents, 7 USDC transfers" in output
    assert "total 0.620000 USDC" in output
    assert swarm.chain.sent == [] and not swarm.file.exists()


def test_approved_run_creates_registers_and_funds_every_agent(swarm):
    code, output = swarm("--i-approve-mainnet")
    assert code == 0 and "DONE" in output
    chain, accounts = swarm.chain, load_swarm_accounts(swarm.file)
    assert stat.S_IMODE(swarm.file.stat().st_mode) == 0o600
    kinds = [entry[0] for entry in chain.sent]
    assert kinds == ["create_account"] + ["create"] * 10 + ["register"] * 10 + ["execute_intents"]
    assert chain.sent[0] == ("create_account", PARENT)
    for role in ROLES:
        account = accounts[role]
        assert chain.accounts[account.account_id]["keys"] == {account.public_key}
        assert (account.account_id, account.public_key) in chain.registered
    assert accounts["parent"].public_key in chain.accounts[PARENT]["keys"]
    assert chain.usdc["owner.arsune-swarm.near"] == 500_000
    assert all(chain.usdc[f"hunter-{i}.arsune-swarm.near"] == 20_000 for i in range(1, 7))
    assert "scout-1.arsune-swarm.near" not in chain.usdc
    assert chain.usdc[RELAYER] == 900_000 - 620_000
    assert output.count("https://nearblocks.io/txns/Tx") == 22

    code, output = swarm("--i-approve-mainnet")
    assert code == 0 and "Nothing to do" in output and len(chain.sent) == 22


def test_interrupted_run_resumes_without_redoing_steps(swarm):
    swarm.chain.fail_after = 4
    code, output = swarm("--i-approve-mainnet")
    assert code == 3 and "Re-run to resume" in output
    first_keys = load_swarm_accounts(swarm.file)
    swarm.chain.fail_after = None
    code, _ = swarm("--i-approve-mainnet")
    assert code == 0
    assert load_swarm_accounts(swarm.file) == first_keys
    created = [entry[1] for entry in swarm.chain.sent if entry[0] in {"create_account", "create"}]
    assert len(created) == len(set(created)) == 11


def test_existing_usdc_is_topped_up_only(swarm):
    swarm.chain.usdc["owner.arsune-swarm.near"] = 400_000
    swarm.chain.usdc["hunter-1.arsune-swarm.near"] = 50_000
    code, output = swarm()
    assert code == 1 and "'owner': 100000" in output and "hunter-1'" not in output
    assert "total 0.200000 USDC" in output


def test_taken_parent_name_is_refused(swarm):
    swarm.chain.accounts[PARENT] = {"keys": {generate_key()[1]}, "near": 10**24}
    code, output = swarm("--i-approve-mainnet")
    assert code == 2 and "taken" in output
    assert swarm.chain.sent == [] and not swarm.file.exists()


@pytest.mark.parametrize(
    "args,change,reason",
    [
        (["--parent", "a.b.near"], {}, "not a valid"),
        (["--parent", "Arsune.near"], {}, "not a valid"),
        (["--owner-usdc", "1000001"], {}, "USDC targets"),
        (["--owner-usdc", "1000000", "--hunter-usdc", "1000000"], {}, "total USDC"),
        ([], {"gas_price": 500_000_000}, "total NEAR"),
        ([], {"relayer_near": 10**23}, "relayer needs"),
        ([], {"relayer_usdc": 600_000}, "too little USDC"),
    ],
)
def test_guards_refuse_before_sending(swarm, args, change, reason):
    if "gas_price" in change:
        swarm.chain.gas_price = change["gas_price"]
    if "relayer_near" in change:
        swarm.chain.accounts[RELAYER]["near"] = change["relayer_near"]
    if "relayer_usdc" in change:
        swarm.chain.usdc[RELAYER] = change["relayer_usdc"]
    code, output = swarm(*args, "--i-approve-mainnet")
    assert code == 2 and reason in output
    assert swarm.chain.sent == [] and not swarm.file.exists()


def test_cost_table_for_the_defaults_stays_inside_the_budget(swarm):
    module = swarm.module
    state = {role: {"exists": False, "registered": False, "usdc": 0} for role in ["parent", *ROLES]}
    args = type("Args", (), {"parent": PARENT, "owner_usdc": 500_000, "hunter_usdc": 20_000})
    transfers = module.top_ups(args, ROLES, state)
    steps = module.plan_steps(args, ROLES, state, transfers)
    total = sum(step.burn(10**8) * step.count for step in steps)
    assert [step.count for step in steps] == [1, 10, 10, 1]
    assert total <= module.MAX_TOTAL_NEAR
    assert max(step.upfront(10**8) for step in steps) == steps[0].upfront(10**8)
