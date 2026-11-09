"""Read-only checks against the real intents.near on mainnet. No transaction is sent.

A throwaway implicit account signs intents and simulate_intents judges them.
Skipped when the RPC is unreachable; deselect with `-m "not network"`.
"""

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.config import settings
from app.settlement import near_intents as ni
from app.settlement.base import SettlementError
from app.settlement.near_tx import NearRpc, generate_key, implicit_account_id, load_signing_key

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def rpc():
    client = NearRpc(settings.near_rpc_url, timeout=15)
    try:
        client.call("status", [])
    except SettlementError as error:
        pytest.skip(f"NEAR RPC unreachable: {error}")
    return client


@pytest.fixture(scope="module")
def throwaway():
    private_key, public_key = generate_key()
    return implicit_account_id(load_signing_key(private_key)), private_key, public_key


def one_unit_transfer(throwaway, rpc, **options):
    account, private_key, _ = throwaway
    receiver = implicit_account_id(load_signing_key(generate_key()[0]))
    deadline = options.pop("deadline", datetime.now(UTC) + timedelta(minutes=5))
    return ni.sign_transfer(
        account, private_key, receiver, settings.bounty_token, 1, "arsune-live-check",
        deadline, rpc=rpc, **options,
    )  # fmt: skip


def simulate_error(signed, rpc):
    with pytest.raises(SettlementError) as caught:
        ni.simulate([signed], rpc=rpc)
    return str(caught.value)


def test_salt_and_balance_views(rpc, throwaway):
    assert len(ni.current_salt(rpc=rpc)) == 4
    assert ni.balance(throwaway[0], rpc=rpc) == 0


def test_valid_transfer_fails_only_for_balance(rpc, throwaway):
    assert "insufficient balance or overflow" in simulate_error(
        one_unit_transfer(throwaway, rpc), rpc
    )


def test_tampered_signature_is_rejected(rpc, throwaway):
    signed = one_unit_transfer(throwaway, rpc)
    raw = bytearray(ni.decode_ed25519(signed["signature"], 64, "signature"))
    raw[10] ^= 1
    signed["signature"] = ni.encode_ed25519(bytes(raw))
    assert "invalid signature" in simulate_error(signed, rpc)


def test_tampered_amount_is_rejected(rpc, throwaway):
    signed = copy.deepcopy(one_unit_transfer(throwaway, rpc))
    signed["payload"]["message"] = signed["payload"]["message"].replace('":"1"}', '":"2"}')
    assert '"2"' in signed["payload"]["message"]
    assert "invalid signature" in simulate_error(signed, rpc)


def test_wrong_salt_and_expired_deadline_are_rejected(rpc, throwaway):
    assert "invalid salt" in simulate_error(
        one_unit_transfer(throwaway, rpc, salt=b"\x00\x00\x00\x00"), rpc
    )
    expired = one_unit_transfer(throwaway, rpc, deadline=datetime.now(UTC) - timedelta(minutes=1))
    assert "deadline has expired" in simulate_error(expired, rpc)


def test_named_account_needs_registered_key(rpc, throwaway):
    _, private_key, public_key = throwaway
    signed = ni.sign_transfer(
        "intents.near", private_key, "arsune.near", settings.bounty_token, 1, "m",
        datetime.now(UTC) + timedelta(minutes=5), rpc=rpc,
    )  # fmt: skip
    assert "doesn't exist for account" in simulate_error(signed, rpc)
    assert ni.has_public_key("intents.near", public_key, rpc=rpc) is False


def test_balance_free_intent_simulates_successfully(rpc, throwaway):
    account, private_key, _ = throwaway
    _, new_public_key = generate_key()
    signed = ni.sign_intents(
        account,
        private_key,
        [{"intent": "add_public_key", "public_key": new_public_key}],
        datetime.now(UTC) + timedelta(minutes=5),
        rpc=rpc,
    )
    result = ni.simulate([signed], rpc=rpc)
    executed = result["intents_executed"]
    assert [(e["account_id"], e["nonce"]) for e in executed] == [
        (account, signed["payload"]["nonce"])
    ]
    assert any(json.loads(log[11:])["event"] == "public_key_added" for log in result["logs"])
