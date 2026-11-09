"""Offline checks of scripts/near_intents_payment.py guards. A fake RPC stands in for mainnet."""

import importlib.util
import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.settlement import near_intents as ni
from app.settlement.near_tx import NearRpcError, generate_key

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "near_intents_payment.py"
ARSUNE = "arsune.near"


class FakeRpc:
    usdc = 5_000_000
    near = 10**24
    sent: tuple = ()  # replaced with a fresh list per test

    def __init__(self, url):
        pass

    def call(self, method, params):
        if method == "gas_price":
            return {"gas_price": "100000000"}
        if method == "query" and params["request_type"] == "view_account":
            if not self.near:
                raise NearRpcError({"name": "HANDLER_ERROR", "cause": {"name": "UNKNOWN_ACCOUNT"}})
            return {"amount": str(self.near)}
        raise AssertionError(method)

    def view(self, contract, method, args):
        if method == "mt_balance_of":
            return str(self.usdc)
        if method == "current_salt":
            return "252812b3"
        if method == "simulate_intents":
            return {"intents_executed": entries(args["signed"])}
        raise AssertionError(method)

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        self.sent.append((receiver_id, method, args, deposit))
        event = {"standard": "dip4", "event": "intents_executed", "data": entries(args["signed"])}
        logs = ["EVENT_JSON:" + json.dumps(event)]
        return "TxHash111", {"receipts_outcome": [{"outcome": {"logs": logs}}]}


def entries(signed_list):
    return [
        {
            "account_id": json.loads(s["payload"]["message"])["signer_id"],
            "nonce": s["payload"]["nonce"],
        }
        for s in signed_list
    ]


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("near_intents_payment", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private_key, _ = generate_key()
    config = Settings(
        _env_file=None,
        near_account_id=ARSUNE,
        near_private_key=private_key,
        near_rpc_url="http://127.0.0.1:9",
    )
    monkeypatch.setattr(module, "settings", config)
    monkeypatch.setattr(ni, "NearRpc", FakeRpc)
    monkeypatch.setattr(FakeRpc, "sent", [])
    return module


def test_without_approval_nothing_is_sent(script, capsys):
    assert script.main(["--receiver", "hunter.near", "--amount", "10000"]) == 1
    output = capsys.readouterr().out
    assert "0.010000 USDC" in output and "Not sent" in output
    assert "ed25519:" not in output
    assert FakeRpc.sent == []


@pytest.mark.parametrize("amount", ["0", "1000001"])
def test_amount_outside_budget_is_refused(script, capsys, amount):
    args = ["--receiver", "hunter.near", "--amount", amount, "--i-approve-mainnet"]
    assert script.main(args) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert FakeRpc.sent == []


def test_missing_settings_or_funds_are_refused(script, monkeypatch, capsys):
    args = ["--receiver", "hunter.near", "--amount", "10000", "--i-approve-mainnet"]
    monkeypatch.setattr(FakeRpc, "near", 0)
    assert script.main(args) == 2
    monkeypatch.setattr(FakeRpc, "near", 10**24)
    monkeypatch.setattr(FakeRpc, "usdc", 9_999)
    assert script.main(args) == 2
    monkeypatch.setattr(script, "settings", Settings(_env_file=None))
    assert script.main(args) == 2
    assert capsys.readouterr().out.count("REFUSED") == 3
    assert FakeRpc.sent == []


def test_approved_payment_attaches_no_deposit(script):
    args = ["--receiver", "hunter.near", "--amount", "10000", "--memo", "m1", "--i-approve-mainnet"]
    assert script.main(args) == 0
    ((receiver, method, call_args, deposit),) = FakeRpc.sent
    assert (receiver, method, deposit) == ("intents.near", "execute_intents", 0)
    ni.verify_signed_transfer(
        call_args["signed"][0],
        sender=ARSUNE,
        receiver="hunter.near",
        token_id=script.settings.bounty_token,
        amount=10000,
        memo="m1",
    )
