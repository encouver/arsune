"""Escrow client: call arguments, deposit messages and signatures, with a fake RPC."""

import base64
import hashlib
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import base58
import pytest
from app.core.config import Settings
from app.settlement import escrow
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.erc191 import generate_evm_key
from app.settlement.near_tx import (
    TGAS,
    NearRpcError,
    generate_key,
    implicit_account_id,
    load_signing_key,
)

TOKEN = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
SALT = bytes.fromhex("252812b3")
ESCROW = "escrow.arsune-swarm.near"
JUDGE = "arsune.near"
HUNTER_KEY, _ = generate_key()
HUNTER = implicit_account_id(load_signing_key(HUNTER_KEY))
RELAYER_KEY, _ = generate_key()
HASH = hashlib.sha256(b"evidence").hexdigest()


def future(minutes=5):
    return datetime.now(UTC) + timedelta(minutes=minutes)


def terms(**changes):
    values = {
        "bounty_id": "6f1c1f5e-7b39-4a4b-9a57-3f0c2b1d9e10",
        "owner": "owner.near",
        "judge": JUDGE,
        "fee_account": JUDGE,
        "reward": 1_000_000,
        "base_bond": 100_000,
        "deadline_ns": 1_800_000_000 * 10**9,
    }
    return escrow.BountyTerms(**(values | changes))


def outcome(value=None, events=(), logs=()):
    lines = list(logs) + [
        "EVENT_JSON:" + json.dumps({"standard": "arsune-escrow", "version": "1.0.0", **e})
        for e in events
    ]
    encoded = base64.b64encode(json.dumps(value).encode()).decode() if value is not None else ""
    return {
        "status": {"SuccessValue": encoded},
        "receipts_outcome": [{"outcome": {"logs": lines}}],
    }


class FakeRpc:
    """Answers views from a dict and records every function call; no network."""

    def __init__(self, views=None, result=None):
        self.views = views or {}
        self.result = result or outcome()
        self.calls = []
        self.viewed = []

    def view(self, contract, method, args):
        self.viewed.append((contract, method, args))
        if method == "current_salt":
            return SALT.hex()
        if method == "simulate_intents":
            entries = [
                {"account_id": signer, "nonce": nonce}
                for signer, nonce in map(ni.signer_and_nonce, args["signed"])
            ]
            return {"intents_executed": entries}
        return self.views[method]

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        self.calls.append(
            {
                "signer": signer_id,
                "receiver": receiver_id,
                "method": method,
                "args": args,
                "gas": gas,
                "deposit": deposit,
            }
        )
        return "TxHash1", self.result


def client(rpc):
    return escrow.EscrowClient(ESCROW, rpc=rpc, intents_contract="intents.near", token_id=TOKEN)


def signed_bond(claim_id="c1", amount=100_000, msg=None, deadline=None):
    msg = msg or escrow.bond_msg("b1", claim_id)
    return escrow.sign_deposit(
        HUNTER, HUNTER_KEY, ESCROW, TOKEN, amount, msg, f"arsune:bond:{claim_id}",
        deadline or future(), salt=SALT, contract="intents.near",
    )  # fmt: skip


def verify(signed, **changes):
    values = {
        "sender": HUNTER,
        "escrow": ESCROW,
        "token_id": TOKEN,
        "amount": 100_000,
        "msg": escrow.bond_msg("b1", "c1"),
        "memo": "arsune:bond:c1",
        "contract": "intents.near",
    }
    escrow.verify_deposit(signed, **(values | changes))


# Terms ------------------------------------------------------------------------


def test_terms_args_use_strings_for_large_numbers():
    args = terms().args()
    assert args == {
        "bounty_id": "6f1c1f5e-7b39-4a4b-9a57-3f0c2b1d9e10",
        "owner": "owner.near",
        "judge": JUDGE,
        "fee_account": JUDGE,
        "reward": "1000000",
        "base_bond": "100000",
        "fee_bps": 1000,
        "scout_share_max_bps": 5000,
        "deadline_ns": "1800000000000000000",
        "rule_timeout_ns": str(24 * 3600 * 10**9),
    }
    t = terms(base_bond=3)
    assert (t.fee, t.min_bond, t.max_bond) == (100_000, 2, 9)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"fee_bps": 2001}, "fee_bps"),
        ({"scout_share_max_bps": 5001}, "scout_share_max_bps"),
        ({"reward": 0}, "reward"),
        ({"reward": 1.5}, "reward"),
        ({"base_bond": 0}, "base_bond"),
        ({"bounty_id": "B1"}, "bounty_id"),
        ({"bounty_id": "x" * 65}, "bounty_id"),
        ({"rule_timeout_ns": 59 * 10**9}, "rule_timeout_ns"),
        ({"rule_timeout_ns": 31 * 24 * 3600 * 10**9}, "rule_timeout_ns"),
        ({"deadline_ns": 0}, "deadline_ns"),
    ],
)
def test_terms_mirror_the_contract_limits(change, error):
    with pytest.raises(SettlementError, match=error):
        terms(**change)


def test_terms_match_the_bounty_view():
    view = terms().args() | {"balance": "0", "status": "open"}
    assert terms().matches(view)
    assert not terms().matches(view | {"fee_account": "mallory.near"})


def test_deadline_and_storage_deposit():
    when = datetime(2026, 11, 14, 23, 59, tzinfo=UTC)
    assert escrow.deadline_ns(when) == int(when.timestamp()) * 10**9
    with pytest.raises(SettlementError):
        escrow.deadline_ns(datetime(2026, 11, 14))
    assert escrow.storage_deposit(100) == (700 + 100 * 600) * 10**19
    with pytest.raises(SettlementError):
        escrow.storage_deposit(0)


# Messages and signed deposits -------------------------------------------------


def test_deposit_messages_are_compact_json():
    assert escrow.fund_msg("b1") == '{"fund":"b1"}'
    assert escrow.bond_msg("b1", "c1") == '{"bond":{"bounty_id":"b1","claim_id":"c1"}}'
    assert escrow.bond_msg("b1", "c1", ("scout.near", 2500)) == (
        '{"bond":{"bounty_id":"b1","claim_id":"c1",'
        '"scout":{"account_id":"scout.near","share_bps":2500}}}'
    )
    for scout in (("scout.near", 0), ("scout.near", 5001)):
        with pytest.raises(SettlementError, match="share_bps"):
            escrow.bond_msg("b1", "c1", scout)
    with pytest.raises(SettlementError, match="claim_id"):
        escrow.bond_msg("b1", "C1")


def test_signed_deposit_carries_msg_and_verifies():
    signed = signed_bond()
    message = json.loads(signed["payload"]["message"])
    assert message["intents"] == [
        {
            "intent": "transfer",
            "receiver_id": ESCROW,
            "tokens": {TOKEN: "100000"},
            "memo": "arsune:bond:c1",
            "msg": '{"bond":{"bounty_id":"b1","claim_id":"c1"}}',
        }
    ]
    verify(signed)


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"msg": escrow.bond_msg("b1", "c2")}, "msg"),
        ({"amount": 100_001}, "tokens"),
        ({"escrow": "other.near"}, "receiver_id"),
        ({"memo": "arsune:bond:c2"}, "memo"),
        ({"sender": "mallory.near"}, "signer_id"),
        ({"contract": "other-intents.near"}, "verifying contract"),
    ],
)
def test_wrong_deposits_are_rejected(change, error):
    with pytest.raises(SettlementError, match=error):
        verify(signed_bond(), **change)


def test_expired_or_tampered_deposit_is_rejected():
    with pytest.raises(SettlementError, match="deadline"):
        verify(signed_bond(), now=future(10))
    signed = signed_bond()
    signed["payload"]["message"] = signed["payload"]["message"].replace("c1", "c2")
    with pytest.raises(SettlementError, match="signature"):
        verify(signed)


def test_deposit_with_extra_intent_keys_is_rejected():
    intent = escrow.deposit_intent(ESCROW, TOKEN, 100_000, escrow.bond_msg("b1", "c1"), "m")
    intent["min_gas"] = "5000000000000"
    signed = ni.sign_intents(HUNTER, HUNTER_KEY, [intent], future(), salt=SALT)
    with pytest.raises(SettlementError, match="deposit intent"):
        verify(signed, memo="m")


def test_arsune_account_transfers_still_refuse_msg():
    # The escrow deposit format must never pass the plain transfer check (no mt_on_transfer
    # side effects on Arsune's own account).
    with pytest.raises(SettlementError, match="exactly the keys"):
        ni.verify_signed_transfer(
            signed_bond(), sender=HUNTER, receiver=ESCROW, token_id=TOKEN, amount=100_000,
            memo="arsune:bond:c1", contract="intents.near",
        )  # fmt: skip


def test_erc191_deposit_verifies():
    private_key, address = generate_evm_key()
    msg = escrow.bond_msg("b1", "c1")
    signed = escrow.sign_deposit_erc191(
        private_key, ESCROW, TOKEN, 100_000, msg, "arsune:bond:c1", future(), SALT,
        contract="intents.near",
    )  # fmt: skip
    verify(signed, sender=address)
    with pytest.raises(SettlementError, match="msg"):
        verify(signed, sender=address, msg=escrow.bond_msg("b1", "c9"))


def test_mt_transfer_call_args():
    assert escrow.mt_transfer_call_args(ESCROW, TOKEN, 5, '{"fund":"b1"}') == {
        "receiver_id": ESCROW,
        "token_id": TOKEN,
        "amount": "5",
        "msg": '{"fund":"b1"}',
    }
    with pytest.raises(SettlementError):
        escrow.mt_transfer_call_args(ESCROW, TOKEN, 0, "{}")


# Rulings and evidence ---------------------------------------------------------


def test_evidence_hash_is_sha256_of_canonical_json():
    bundle = {"claim_id": "c1", "verdict": "proven", "evidence": [{"url": "https://a/é"}]}
    reordered = {"verdict": "proven", "evidence": [{"url": "https://a/é"}], "claim_id": "c1"}
    expected = hashlib.sha256(
        '{"claim_id":"c1","evidence":[{"url":"https://a/é"}],"verdict":"proven"}'.encode()
    ).hexdigest()
    assert escrow.evidence_hash(bundle) == escrow.evidence_hash(reordered) == expected
    assert escrow.evidence_hash(b"raw") == hashlib.sha256(b"raw").hexdigest()


@pytest.mark.parametrize(
    ("outcome", "evidence", "error"),
    [
        ("expired", HASH, "outcome"),
        ("paid", HASH, "outcome"),
        ("proven", HASH.upper(), "evidence_hash"),
        ("proven", HASH[:-1], "evidence_hash"),
    ],
)
def test_rule_args_are_checked(outcome, evidence, error):
    with pytest.raises(SettlementError, match=error):
        escrow.rule_args("c1", outcome, evidence)


def test_events_are_read_from_every_receipt():
    result = outcome(
        events=[{"event": "rule", "data": {"claim_id": "c1", "outcome": "proven"}}],
        logs=["plain log", 'EVENT_JSON:{"standard":"nep245","event":"mt_transfer","data":[]}'],
    )
    assert escrow.escrow_events(result) == [
        {"event": "rule", "claim_id": "c1", "outcome": "proven"}
    ]


# Client calls ------------------------------------------------------------------


def test_views_use_the_escrow_contract():
    rpc = FakeRpc(views={"get_bounty": {"bounty_id": "b1"}, "list_claims": [], "get_owed": "7"})
    c = client(rpc)
    assert c.get_bounty("b1") == {"bounty_id": "b1"}
    assert c.list_claims("b1", 100, 50) == []
    assert c.get_owed("hunter.near") == 7
    assert rpc.viewed[1] == (
        ESCROW,
        "list_claims",
        {"bounty_id": "b1", "from_index": 100, "limit": 50},
    )


def test_check_terms_refuses_a_squatted_bounty():
    rpc = FakeRpc(views={"get_bounty": terms(owner="mallory.near").args()})
    with pytest.raises(SettlementError, match="other terms"):
        client(rpc).check_terms(terms())
    rpc = FakeRpc(views={"get_bounty": None})
    with pytest.raises(SettlementError, match="does not exist"):
        client(rpc).check_terms(terms())
    rpc = FakeRpc(views={"get_bounty": terms().args(), "get_config": {"token_id": TOKEN}})
    assert client(rpc).check_terms(terms())["owner"] == "owner.near"


def test_create_bounty_attaches_storage_for_claims():
    rpc = FakeRpc(result=outcome({"bounty_id": "b1"}, [{"event": "create", "data": {}}]))
    key = load_signing_key(RELAYER_KEY)
    call = client(rpc).create_bounty(JUDGE, key, terms(), claims=150)
    (sent,) = rpc.calls
    assert sent["receiver"] == ESCROW
    assert sent["method"] == "create_bounty"
    assert sent["args"] == terms().args()
    assert sent["deposit"] == (700 + 150 * 600) * 10**19
    assert sent["gas"] == 30 * TGAS
    assert call.result == {"bounty_id": "b1"}
    assert call.events == [{"event": "create"}]
    with pytest.raises(SettlementError, match="owner or the judge"):
        client(rpc).create_bounty("mallory.near", key, terms(), claims=1)


def test_rule_sends_outcome_and_evidence_hash():
    rule_event = {"event": "rule", "data": {"claim_id": "c1", "outcome": "proven"}}
    rpc = FakeRpc(result=outcome({"claim_id": "c1", "outcome": "proven"}, [rule_event]))
    call = client(rpc).rule(JUDGE, load_signing_key(RELAYER_KEY), "c1", "proven", HASH)
    (sent,) = rpc.calls
    assert sent["args"] == {"claim_id": "c1", "outcome": "proven", "evidence_hash": HASH}
    assert sent["gas"] == 110 * TGAS
    assert sent["deposit"] == 0
    assert call.result["outcome"] == "proven"
    rpc = FakeRpc(result=outcome(None))
    with pytest.raises(SettlementError, match="no rule event"):
        client(rpc).rule(JUDGE, load_signing_key(RELAYER_KEY), "c1", "proven", HASH)


def test_owner_and_liveness_calls():
    rpc = FakeRpc()
    c = client(rpc)
    key = load_signing_key(RELAYER_KEY)
    c.close_bounty("owner.near", key, "b1")
    c.withdraw_remaining("anyone.near", key, "b1")
    c.reclaim_bond("anyone.near", key, "c1")
    c.retry_payout("anyone.near", key, "hunter.near")
    assert [(s["method"], s["args"], s["deposit"]) for s in rpc.calls] == [
        ("close_bounty", {"bounty_id": "b1"}, 1),
        ("withdraw_remaining", {"bounty_id": "b1"}, 0),
        ("reclaim_bond", {"claim_id": "c1"}, 0),
        ("retry_payout", {"account_id": "hunter.near"}, 0),
    ]


def test_direct_deposit_uses_mt_transfer_call_and_reports_refunds():
    bond_event = {"event": "bond", "data": {"claim_id": "c1"}}
    rpc = FakeRpc(result=outcome(["100000"], [bond_event]))
    key = load_signing_key(HUNTER_KEY)
    call = client(rpc).deposit(HUNTER, key, 100_000, escrow.bond_msg("b1", "c1"))
    (sent,) = rpc.calls
    assert sent["receiver"] == "intents.near"
    assert sent["method"] == "mt_transfer_call"
    assert sent["deposit"] == 1
    assert call.result == ["100000"]
    refund = {"event": "refund", "data": {"reason": "bond_out_of_range"}}
    rpc = FakeRpc(result=outcome(["0"], [refund]))
    with pytest.raises(SettlementError, match="bond_out_of_range"):
        client(rpc).deposit(HUNTER, key, 1, escrow.bond_msg("b1", "c1"))


def executed(signed):
    signer, nonce = ni.signer_and_nonce(signed)
    data = [{"account_id": signer, "nonce": nonce, "intent_hash": "h"}]
    event = {"standard": "dip4", "event": "intents_executed", "data": data}
    return "EVENT_JSON:" + json.dumps(event)


def relay(rpc, signed):
    return client(rpc).relay_deposit(
        signed, sender=HUNTER, amount=100_000, msg=escrow.bond_msg("b1", "c1"),
        memo="arsune:bond:c1", relayer_id=JUDGE, private_key=RELAYER_KEY,
    )  # fmt: skip


def test_relay_deposit_verifies_simulates_and_executes():
    signed = signed_bond()
    bond_event = {"event": "bond", "data": {"claim_id": "c1", "hunter": HUNTER}}
    rpc = FakeRpc(result=outcome(None, [bond_event], logs=[executed(signed)]))
    call = relay(rpc, signed)
    assert [v[1] for v in rpc.viewed] == ["simulate_intents"]
    (sent,) = rpc.calls
    assert (sent["signer"], sent["receiver"], sent["method"]) == (
        JUDGE,
        "intents.near",
        "execute_intents",
    )
    assert sent["args"] == {"signed": [signed]}
    assert sent["gas"] == 100 * TGAS
    assert call.events == [{"event": "bond", "claim_id": "c1", "hunter": HUNTER}]


def test_relay_deposit_raises_when_escrow_refunds_or_nothing_happens():
    signed = signed_bond()
    refund = {"event": "refund", "data": {"reason": "budget_too_low"}}
    rpc = FakeRpc(result=outcome(None, [refund], logs=[executed(signed)]))
    with pytest.raises(SettlementError, match="budget_too_low"):
        relay(rpc, signed)
    rpc = FakeRpc(result=outcome(None, [], logs=[executed(signed)]))
    with pytest.raises(SettlementError, match="no escrow deposit event"):
        relay(rpc, signed)
    rpc = FakeRpc(result=outcome(None, [{"event": "bond", "data": {}}]))
    with pytest.raises(SettlementError, match="did not execute"):
        relay(rpc, signed)


def test_relay_deposit_rejects_a_wrong_signed_deposit_before_any_rpc():
    rpc = FakeRpc()
    with pytest.raises(SettlementError, match="msg"):
        relay(rpc, signed_bond(msg=escrow.bond_msg("b1", "c9")))
    assert rpc.calls == [] and rpc.viewed == []


# Deploy script, offline -------------------------------------------------------

DEPLOY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "near_escrow_deploy.py"
BUILT_WASM = Path(__file__).resolve().parents[2] / "contracts/escrow/target/near/arsune_escrow.wasm"


def fake_wasm(exports):
    def leb(value):
        out = b""
        while True:
            byte, value = value & 0x7F, value >> 7
            out += bytes([byte | (0x80 if value else 0)])
            if not value:
                return out

    body = leb(len(exports)) + b"".join(
        leb(len(name)) + name.encode() + b"\x00" + leb(0) for name in sorted(exports)
    )
    return b"\0asm\x01\0\0\0" + b"\x07" + leb(len(body)) + body + b"\x0a\x01\x00"


class DeployRpc:
    """Mainnet as the deploy script sees it: the escrow name is free until the deploy."""

    near = 10 * 10**24

    def __init__(self, url):
        self.sent = []
        self.code_hash = None
        DeployRpc.last = self

    def call(self, method, params):
        if method == "gas_price":
            return {"gas_price": "100000000"}
        kind, account = params.get("request_type"), params.get("account_id")
        if kind == "view_account":
            if account.startswith("escrow.") and self.code_hash is None:
                raise NearRpcError({"name": "HANDLER_ERROR", "cause": {"name": "UNKNOWN_ACCOUNT"}})
            return {"amount": str(self.near), "code_hash": self.code_hash}
        if kind == "view_access_key":
            return {"nonce": 1, "block_hash": "11111111111111111111111111111111"}
        if kind == "view_access_key_list":
            return {"keys": []}
        raise AssertionError((method, params))

    def view(self, contract, method, args):
        assert method == "get_config"
        return {"intents_contract": "intents.near", "token_id": TOKEN}

    def send_actions(self, signer_id, key, receiver_id, actions):
        self.sent.append((signer_id, receiver_id, actions))
        self.code_hash = DeployRpc.expected_hash
        return "TxDeploy", {}


@pytest.fixture
def deploy_script(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("near_escrow_deploy", DEPLOY_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private_key, _ = generate_key()
    config = Settings(
        _env_file=None,
        near_account_id="arsune.near",
        near_private_key=private_key,
        near_rpc_url="http://127.0.0.1:9",
    )
    monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(module, "NearRpc", DeployRpc)
    wasm = tmp_path / "escrow.wasm"
    wasm.write_bytes(fake_wasm(module.EXPORTS))
    DeployRpc.expected_hash = base58.b58encode(hashlib.sha256(wasm.read_bytes()).digest()).decode()
    module.test_args = ["--wasm", str(wasm), "--parent", "arsune.near"]
    return module


def test_deploy_script_knows_the_built_exports(deploy_script):
    if not BUILT_WASM.exists():
        pytest.skip("contracts/escrow/build.sh has not been run")
    assert deploy_script.wasm_exports(BUILT_WASM.read_bytes()) == deploy_script.EXPORTS


def test_deploy_plan_sends_nothing(deploy_script, capsys):
    assert deploy_script.main(deploy_script.test_args) == 0
    output = capsys.readouterr().out
    assert "escrow.arsune.near (new, no access key, locked)" in output
    assert "Not sent" in output
    assert DeployRpc.last.sent == []


def test_deploy_costs_follow_code_size(deploy_script):
    size = 223_323
    storage = deploy_script.escrow_storage(size, 5 * 10**22)
    assert storage == (size + 500) * 10**19 + 5 * 10**22
    gas = deploy_script.deploy_gas(size)
    assert 50 * TGAS < gas < 70 * TGAS


@pytest.mark.parametrize(
    ("args", "near", "reason"),
    [
        (["--max-near", str(10**22)], 10 * 10**24, "above --max-near"),
        ([], 5 * 10**22, "the relayer needs"),
    ],
)
def test_deploy_refusals(deploy_script, capsys, monkeypatch, args, near, reason):
    monkeypatch.setattr(DeployRpc, "near", near)
    assert deploy_script.main(deploy_script.test_args + args + ["--i-approve-mainnet"]) == 2
    assert reason in capsys.readouterr().out
    assert DeployRpc.last.sent == []


def test_deploy_refuses_other_wasm(deploy_script, capsys, tmp_path):
    other = tmp_path / "other.wasm"
    other.write_bytes(fake_wasm(deploy_script.EXPORTS | {"upgrade"}))
    args = ["--wasm", str(other), "--parent", "arsune.near", "--i-approve-mainnet"]
    assert deploy_script.main(args) == 2
    assert "unexpected wasm exports: ['upgrade']" in capsys.readouterr().out


def test_deploy_is_one_locked_transaction(deploy_script, capsys):
    assert deploy_script.main(deploy_script.test_args + ["--i-approve-mainnet"]) == 0
    ((signer, receiver, actions),) = DeployRpc.last.sent
    assert (signer, receiver) == ("arsune.near", "escrow.arsune.near")
    assert [action[0] for action in actions] == [0, 3, 1, 2]  # create, transfer, deploy, call
    assert all(action[0] != 5 for action in actions)  # no AddKey: the account stays locked
    assert actions[2][5:] == (Path(deploy_script.test_args[1])).read_bytes()
    assert b'{"intents_contract":"intents.near","token_id":"' + TOKEN.encode() in actions[3]
    assert "Deployed and locked" in capsys.readouterr().out
