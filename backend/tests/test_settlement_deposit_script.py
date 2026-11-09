"""Offline checks of scripts/near_deposit_usdc.py. A fake RPC stands in for mainnet."""

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from app.core.config import Settings
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    TGAS,
    OutcomeUnknown,
    generate_key,
    implicit_account_id,
    load_signing_key,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "near_deposit_usdc.py"
TOKEN = "17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
PRIVATE_KEY, _ = generate_key()
ACCOUNT = implicit_account_id(load_signing_key(PRIVATE_KEY))


class FakeRpc:
    """Mainnet stand-in: answers views and fakes ft_transfer_call; never opens a socket."""

    def __init__(self, url):
        self.registered = True
        self.wallet = 2_000_000
        self.intents = 0
        self.near = 10**23
        self.gas_price = 100_000_000
        self.keep = None  # amount intents.near keeps; None means all
        self.fail = False
        self.sent = []

    def call(self, method, params):
        if method == "gas_price":
            return {"gas_price": str(self.gas_price)}
        if method == "query" and params["request_type"] == "view_account":
            return {"amount": str(self.near)}
        raise AssertionError(method)

    def view(self, contract, method, args):
        answers = {
            (TOKEN, "storage_balance_of"): {"total": "1", "available": "0"}
            if self.registered
            else None,
            (TOKEN, "storage_balance_bounds"): {"min": "1250000000000000000000"},
            (TOKEN, "ft_balance_of"): str(self.wallet),
            ("intents.near", "mt_balance_of"): str(self.intents),
        }
        return answers[(contract, method)]

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        self.sent.append((signer_id, receiver_id, method, args, gas, deposit))
        if self.fail:
            raise self.fail
        kept = int(args["amount"]) if self.keep is None else self.keep
        self.wallet -= kept
        self.intents += kept
        value = base64.b64encode(json.dumps(str(kept)).encode()).decode()
        return "TxHash111", {"status": {"SuccessValue": value}}


@pytest.fixture
def run(monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("near_deposit_usdc", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = Settings(
        _env_file=None,
        near_account_id=ACCOUNT,
        near_private_key=PRIVATE_KEY,
        near_rpc_url="http://127.0.0.1:9",
    )
    monkeypatch.setattr(module, "settings", config)
    rpc = FakeRpc(None)
    monkeypatch.setattr(module, "NearRpc", lambda url: rpc)

    def main(*args, **changes):
        for name, value in changes.items():
            setattr(rpc, name, value)
        code = module.main(list(args))
        output = capsys.readouterr().out
        assert PRIVATE_KEY[8:] not in output and "ed25519:" not in output
        return code, output, rpc

    main.module = module
    return main


def test_without_approval_only_the_plan_is_printed(run):
    code, output, rpc = run("--amount", "1000000")
    assert code == 1 and "Not sent" in output
    assert '"msg": ""' in output and "1 yoctoNEAR" in output and "50 Tgas" in output
    assert "2000000 (2.000000 USDC)" in output
    assert rpc.sent == []


@pytest.mark.parametrize("amount", ["0", "-5", "1000001"])
def test_amount_outside_budget_is_refused(run, amount):
    code, output, rpc = run("--amount", amount, "--i-approve-mainnet")
    assert code == 2 and "REFUSED" in output
    assert rpc.sent == []


def test_amount_above_wallet_balance_is_refused(run):
    code, output, rpc = run("--amount", "600000", "--i-approve-mainnet", wallet=500_000)
    assert code == 2 and "USDC balance" in output
    assert rpc.sent == []


def test_missing_storage_registration_prints_the_fix(run):
    code, output, rpc = run("--amount", "1000", "--i-approve-mainnet", registered=False)
    assert code == 2
    assert f"call {TOKEN} storage_deposit" in output
    assert f'{{"account_id": "{ACCOUNT}", "registration_only": true}}' in output
    assert "0.00125 NEAR" in output
    assert rpc.sent == []


def test_gas_limits_are_enforced(run):
    code, output, rpc = run("--amount", "1000", "--i-approve-mainnet", gas_price=500_000_000)
    assert code == 2 and "gas could cost" in output
    code, output, rpc = run("--amount", "1000", "--i-approve-mainnet", gas_price=10**8, near=0)
    assert code == 2 and "not enough NEAR" in output
    assert rpc.sent == []


def test_missing_settings_are_refused(run, monkeypatch):
    monkeypatch.setattr(run.module, "settings", Settings(_env_file=None))
    code, output, rpc = run("--amount", "1000", "--i-approve-mainnet")
    assert code == 2 and "ARSUNE_NEAR_ACCOUNT_ID" in output
    assert rpc.sent == []


def test_approved_deposit_sends_one_bounded_call_and_checks_the_credit(run):
    code, output, rpc = run("--amount", "1000000", "--i-approve-mainnet")
    assert code == 0 and "DEPOSITED" in output
    assert "https://nearblocks.io/txns/TxHash111" in output
    assert "1000000 (1.000000 USDC)" in output
    call_args = {"receiver_id": "intents.near", "amount": "1000000", "msg": ""}
    assert rpc.sent == [(ACCOUNT, TOKEN, "ft_transfer_call", call_args, 50 * TGAS, 1)]


def test_partial_refund_or_failed_step_is_reported(run):
    code, output, _ = run("--amount", "1000000", "--i-approve-mainnet", keep=0)
    assert code == 3 and "not fully credited" in output
    failed = SettlementError("transaction X has a failed step")
    code, output, _ = run("--amount", "1000", "--i-approve-mainnet", keep=None, fail=failed)
    assert code == 3 and "FAILED: transaction X has a failed step" in output
    lost = OutcomeUnknown("no final outcome from NEAR RPC", "TxLost")
    code, output, _ = run("--amount", "1000", "--i-approve-mainnet", fail=lost)
    assert code == 3 and "https://nearblocks.io/txns/TxLost before retrying" in output
