"""Offline tests for NEAR Intents settlement: published vectors, signing, checks, guardrails."""

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta

import base58
import pytest
from app.core.config import Settings
from app.settlement import near_intents as ni
from app.settlement import near_tx
from app.settlement.base import SettlementError
from app.settlement.near_tx import (
    NearRpc,
    NearRpcError,
    OutcomeUnknown,
    RpcUnavailable,
    check_outcome,
    function_call_tx,
    generate_key,
    implicit_account_id,
    load_signing_key,
    sign_tx,
)
from nacl.signing import VerifyKey

TOKEN = "nep141:17208628f84f5d6ad33f0da3bbbeb27ffcb398eac501a31bd6ad2011e36133a1"
SALT = bytes.fromhex("252812b3")
ARSUNE = "arsune.near"
HUNTER_KEY, HUNTER_PUBLIC = generate_key()
HUNTER = implicit_account_id(load_signing_key(HUNTER_KEY))


def future(minutes=5):
    return datetime.now(UTC) + timedelta(minutes=minutes)


def hunter_bond(receiver_id=ARSUNE, token_id=TOKEN, amount=50_000, memo="bond:claim-1"):
    return ni.sign_transfer(
        HUNTER, HUNTER_KEY, receiver_id, token_id, amount, memo, future(), salt=SALT
    )


def verify(signed, sender=HUNTER, memo="bond:claim-1"):
    ni.verify_signed_transfer(
        signed, sender=sender, receiver=ARSUNE, token_id=TOKEN, amount=50_000, memo=memo
    )


# Published vectors -----------------------------------------------------------


def test_nep413_vector_from_defuse_nep413_crate():
    # near/intents crates/signatures/nep413/src/lib.rs, test verify_ok
    public_key = bytes.fromhex("e2e9cb7ac57cb46d4da1ce1d1cc2c33bdfe17407c517916b522724a8ea2c6c50")
    signature = bytes.fromhex(
        "e2ff6254871a3fec1853c167b42f0f14248c4cf7fef5452dc24d8dbdc5c4bf18"
        "3ab707322b4d782d5f5a05571bae476c5f7ee41c473f3002e600865e46b75d0f"
    )
    digest = ni.nep413_hash("Hello, world!", bytes(32), "intents.near")
    VerifyKey(public_key).verify(digest, signature)


def test_signed_transfer_example_from_near_intents_docs():
    # docs.near-intents.org/integration/verifier-contract/signing-intents.md (NEP-413 section)
    message = (
        '{"deadline":"2025-05-21T10:34:04.254392Z","intents":[{"intent":"transfer",'
        '"receiver_id":"bob.near","tokens":{"nep141:usdc.near":"10"}}],"signer_id":"alice.near"}'
    )
    nonce = base64.b64decode("Vij2xgAlKBKzgNPJFViEQRgYyS7p2NEiYTTY4XmT8go=")
    public_key = ni.decode_ed25519("ed25519:C3jXhkGhEx88Gj7XKtUziJKXEBMRaJ67bWFkxJikVxZ2", 32, "pk")
    signature = ni.decode_ed25519(
        "ed25519:2ns3eCikZFxFA6e9ApunuDG4XwJUPrB8q82fYViBKjheqiDYcCmLN9RnB9NhoEvCdNoS3L3qYD9FAnKd7UkTnuij",
        64,
        "signature",
    )
    VerifyKey(public_key).verify(ni.nep413_hash(message, nonce, "intents.near"), signature)


def test_versioned_nonce_matches_defuse_readme_example(monkeypatch):
    # near/intents contracts/defuse/README.md, "Versioned nonce" example
    random_part = bytes.fromhex("027015ec13dc11864973138fe6812f")
    monkeypatch.setattr(ni.secrets, "token_bytes", lambda size: random_part[:size])
    nonce = ni.versioned_nonce(SALT, datetime.fromtimestamp(2280047743, UTC))
    assert base64.b64encode(nonce).decode() == "Vij2xgAlKBKzADZykFdbpB8CcBXsE9wRhklzE4/mgS8="


def test_function_call_tx_matches_near_api_js_vector():
    # near/near-api-js test/unit/transactions/data/transaction1.json
    expected = (
        "0000000000795cb7b5f57222e742d1759092f0e20071a0cd2bf30e1f681d800e67935e1688010000000000"
        "00001000000073747564696f2d76776375396534316d4def837b838543990f3380af8e2a3817ddf70fe996"
        "0135b2add25a679b2a01ed01000000020a0000006164644d6573736167650b0000007b2274657874223a22"
        "227d80841e000000000000000000000000000000000000000000"
    )
    tx = function_call_tx(
        signer_id="",
        public_key=bytes.fromhex(
            "795cb7b5f57222e742d1759092f0e20071a0cd2bf30e1f681d800e67935e1688"
        ),
        nonce=1,
        receiver_id="studio-vwcu9e41m",
        block_hash=bytes.fromhex(
            "4def837b838543990f3380af8e2a3817ddf70fe9960135b2add25a679b2a01ed"
        ),
        method="addMessage",
        args=b'{"text":""}',
        gas=2_000_000,
        deposit=0,
    )
    assert tx.hex() == expected


def test_transactions_sign_sha256_of_borsh_like_near_api_js():
    # near/near-api-js test/unit/transactions/data/signed_transaction1.json
    signed = bytes.fromhex(
        "09000000746573742e6e65617200917b3d268d4b58f7fec1b150bd68d69be3ee5d4cc39855e341538465bb"
        "77860d01000000000000000d00000077686174657665722e6e6561720fa473fd26901df296be6adc4cc4df"
        "34d040efa2435224b6986910e630c2fef6010000000301000000000000000000000000000000009"
        "69a83332186ee9755e4839325525806e189a3d2d2bb4b4760e94443e97e1c4f22deeef0059a8e9713100eda"
        "6e19144da7e8a0ef7e539b20708ba1d8d021bd01"
    )
    tx, tag, signature = signed[:-65], signed[-65], signed[-64:]
    public_key = base58.b58decode("Anu7LYDfpLtkP7E16LT9imXF694BdQaa9ufVkQiwTQxC")
    assert tag == 0
    VerifyKey(public_key).verify(hashlib.sha256(tx).digest(), signature)

    key = load_signing_key(HUNTER_KEY)
    tx_hash, ours = sign_tx(tx, key)
    assert ours[: len(tx)] == tx and ours[len(tx)] == 0
    assert tx_hash == base58.b58encode(hashlib.sha256(tx).digest()).decode()
    key.verify_key.verify(hashlib.sha256(tx).digest(), ours[-64:])


# Keys ------------------------------------------------------------------------


def test_private_key_forms():
    key = load_signing_key(HUNTER_KEY)
    seed_only = "ed25519:" + base58.b58encode(bytes(key)).decode()
    assert bytes(load_signing_key(seed_only).verify_key) == bytes(key.verify_key)
    other, _ = generate_key()
    mismatched = (
        "ed25519:" + base58.b58encode(bytes(key) + base58.b58decode(other[8:])[32:]).decode()
    )
    with pytest.raises(SettlementError, match="public half"):
        load_signing_key(mismatched)
    with pytest.raises(SettlementError):
        load_signing_key("secp256k1:abc")


# Sign and verify -------------------------------------------------------------


def test_sign_verify_round_trip_and_payload_layout():
    signed = hunter_bond()
    verify(signed)
    assert signed["standard"] == "nep413"
    assert signed["payload"]["recipient"] == "intents.near"
    assert signed["public_key"] == HUNTER_PUBLIC
    nonce = base64.b64decode(signed["payload"]["nonce"])
    assert nonce[:5] == bytes.fromhex("5628f6c600") and nonce[5:9] == SALT
    message = json.loads(signed["payload"]["message"])
    assert message["signer_id"] == HUNTER
    assert message["intents"] == [
        {
            "intent": "transfer",
            "receiver_id": ARSUNE,
            "tokens": {TOKEN: "50000"},
            "memo": "bond:claim-1",
        }
    ]


def edit_message(signed, edit):
    tampered = copy.deepcopy(signed)
    message = json.loads(tampered["payload"]["message"])
    edit(message)
    tampered["payload"]["message"] = json.dumps(message, separators=(",", ":"))
    return tampered


def set_intent(field, value):
    return lambda message: message["intents"][0].__setitem__(field, value)


@pytest.mark.parametrize(
    "edit",
    [
        set_intent("tokens", {TOKEN: "50001"}),
        set_intent("memo", "bond:claim-2"),
        set_intent("receiver_id", "thief.near"),
        lambda message: message.__setitem__("signer_id", "someone.near"),
        lambda message: message.__setitem__("deadline", "2099-01-01T00:00:00Z"),
    ],
    ids=["amount", "memo", "receiver", "signer", "deadline"],
)
def test_tampered_message_breaks_signature(edit):
    with pytest.raises(SettlementError, match="signature"):
        verify(edit_message(hunter_bond(), edit))


def test_tampered_signature_and_key_fail():
    signed = hunter_bond()
    raw = bytearray(ni.decode_ed25519(signed["signature"], 64, "signature"))
    raw[0] ^= 1
    with pytest.raises(SettlementError, match="signature"):
        verify({**signed, "signature": ni.encode_ed25519(bytes(raw))})
    _, other_public = generate_key()
    with pytest.raises(SettlementError, match="signature"):
        verify({**signed, "public_key": other_public})
    with pytest.raises(SettlementError, match="public_key"):
        verify({**signed, "public_key": "ed25519:abc"})


@pytest.mark.parametrize(
    "signed_with,expected,error",
    [
        ({"amount": 49_999}, {}, "amount"),
        ({"memo": "bond:claim-2"}, {}, "memo"),
        ({"receiver_id": "thief.near"}, {}, "receiver"),
        ({"token_id": "nep141:usdt.tether-token.near"}, {}, "token"),
        ({}, {"sender": "someone.near"}, "signer_id"),
    ],
)
def test_correctly_signed_but_wrong_transfer_fails(signed_with, expected, error):
    with pytest.raises(SettlementError, match=error):
        verify(hunter_bond(**signed_with), **expected)


def test_expired_deadline_fails():
    signed = ni.sign_transfer(
        HUNTER, HUNTER_KEY, ARSUNE, TOKEN, 50_000, "bond:claim-1", future(-1), salt=SALT
    )
    with pytest.raises(SettlementError, match="deadline"):
        verify(signed)
    later = datetime.now(UTC) + timedelta(minutes=10)
    with pytest.raises(SettlementError, match="deadline"):
        ni.verify_signed_transfer(
            hunter_bond(),
            sender=HUNTER,
            receiver=ARSUNE,
            token_id=TOKEN,
            amount=50_000,
            memo="bond:claim-1",
            now=later,
        )


def test_extra_fields_and_other_contracts_are_rejected():
    def sign(intents, contract="intents.near"):
        return ni.sign_intents(HUNTER, HUNTER_KEY, intents, future(), salt=SALT, contract=contract)

    transfer = json.loads(hunter_bond()["payload"]["message"])["intents"][0]
    with pytest.raises(SettlementError, match="exactly the keys"):
        verify(sign([{**transfer, "msg": "call receiver"}]))
    with pytest.raises(SettlementError, match="exactly one intent"):
        verify(sign([transfer, transfer]))
    with pytest.raises(SettlementError, match="verifying contract"):
        verify(sign([transfer], contract="intents.testnet"))
    with pytest.raises(SettlementError, match="nep413"):
        verify({**hunter_bond(), "standard": "erc191"})
    with pytest.raises(SettlementError, match="exactly the keys"):
        verify({**hunter_bond(), "extra": 1})


def test_duplicate_json_keys_are_rejected():
    signed = hunter_bond()
    message = signed["payload"]["message"]
    doubled = message[:-1] + ',"signer_id":"someone.near"}'
    key = load_signing_key(HUNTER_KEY)
    nonce = base64.b64decode(signed["payload"]["nonce"])
    signature = key.sign(ni.nep413_hash(doubled, nonce, "intents.near")).signature
    forged = copy.deepcopy(signed)
    forged["payload"]["message"] = doubled
    forged["signature"] = ni.encode_ed25519(signature)
    with pytest.raises(SettlementError, match="duplicate"):
        verify(forged)


@pytest.mark.parametrize("amount", [0, -1, True, 1.5, "10"])
def test_bad_amounts_are_rejected(amount):
    with pytest.raises(SettlementError, match="amount"):
        ni.sign_transfer(HUNTER, HUNTER_KEY, ARSUNE, TOKEN, amount, "m", future(), salt=SALT)


# Settlement with a fake RPC --------------------------------------------------


class FakeRpc:
    """Answers the Verifier views and records execute_intents calls; never touches the network."""

    def __init__(self, logs_for=None):
        self.calls = []
        self.logs_for = logs_for

    def view(self, contract, method, args):
        if method == "current_salt":
            return SALT.hex()
        if method == "simulate_intents":
            return {"intents_executed": executed_entries(args["signed"]), "state": {}}
        raise AssertionError(method)

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        self.calls.append((signer_id, receiver_id, method, args, deposit))
        entries = executed_entries(args["signed"] if self.logs_for is None else self.logs_for)
        event = {"standard": "dip4", "event": "intents_executed", "data": entries}
        outcome = {"receipts_outcome": [{"outcome": {"logs": ["EVENT_JSON:" + json.dumps(event)]}}]}
        return "TxHash111", outcome


def executed_entries(signed_list):
    return [
        {
            "account_id": json.loads(signed["payload"]["message"])["signer_id"],
            "nonce": signed["payload"]["nonce"],
            "intent_hash": "h",
        }
        for signed in signed_list
    ]


def arsune_settings(max_payment_amount=1_000_000):
    arsune_key, _ = generate_key()
    return Settings(
        _env_file=None,
        near_account_id=ARSUNE,
        near_private_key=arsune_key,
        max_payment_amount=max_payment_amount,
    )


def test_settlement_needs_account_and_key():
    with pytest.raises(SettlementError, match="ARSUNE_NEAR_ACCOUNT_ID"):
        ni.NearIntentsSettlement(Settings(_env_file=None), rpc=FakeRpc())


def test_collect_verifies_simulates_and_executes_the_bond():
    rpc = FakeRpc()
    settlement = ni.NearIntentsSettlement(arsune_settings(), rpc=rpc)
    bond = hunter_bond()
    assert settlement.mode == "near_intents"
    assert (
        settlement.collect(bond, sender=HUNTER, amount=50_000, memo="bond:claim-1") == "TxHash111"
    )
    assert rpc.calls == [(ARSUNE, "intents.near", "execute_intents", {"signed": [bond]}, 0)]
    with pytest.raises(SettlementError, match="memo"):
        settlement.collect(bond, sender=HUNTER, amount=50_000, memo="bond:claim-2")
    assert len(rpc.calls) == 1


def test_pay_signs_transfer_with_verbatim_memo():
    rpc = FakeRpc()
    config = arsune_settings()
    settlement = ni.NearIntentsSettlement(config, rpc=rpc)
    memo = "payout:claim-1:ünïcode"
    assert settlement.pay(HUNTER, 250_000, memo) == "TxHash111"
    (signed,) = rpc.calls[0][3]["signed"]
    arsune_public = ni.public_key_text(load_signing_key(config.near_private_key))
    assert signed["public_key"] == arsune_public
    ni.verify_signed_transfer(
        signed, sender=ARSUNE, receiver=HUNTER, token_id=TOKEN, amount=250_000, memo=memo
    )


def test_amount_guardrail_blocks_before_any_rpc():
    rpc = FakeRpc()
    settlement = ni.NearIntentsSettlement(arsune_settings(max_payment_amount=100_000), rpc=rpc)
    with pytest.raises(SettlementError, match="max_payment_amount"):
        settlement.pay(HUNTER, 100_001, "payout:big")
    with pytest.raises(SettlementError, match="max_payment_amount"):
        settlement.collect(
            hunter_bond(amount=100_001), sender=HUNTER, amount=100_001, memo="bond:claim-1"
        )
    with pytest.raises(SettlementError, match="amount"):
        settlement.pay(HUNTER, 0, "payout:zero")
    with pytest.raises(SettlementError, match="own account"):
        settlement.pay(ARSUNE, 1, "payout:self")
    assert rpc.calls == []
    assert settlement.pay(HUNTER, 100_000, "payout:limit") == "TxHash111"


def test_execute_requires_every_intent_in_the_executed_events():
    signed = hunter_bond()
    rpc = FakeRpc(logs_for=[])
    with pytest.raises(SettlementError, match="without executing"):
        ni.execute([signed], account_id=ARSUNE, private_key=HUNTER_KEY, rpc=rpc)


# Transaction outcome handling -----------------------------------------------


def outcome(tx_hash="abc", status=None, receipt_status=None):
    return {
        "final_execution_status": "FINAL",
        "transaction": {"hash": tx_hash},
        "status": status or {"SuccessValue": ""},
        "transaction_outcome": {"outcome": {"status": {"SuccessReceiptId": "r1"}, "logs": []}},
        "receipts_outcome": [
            {"outcome": {"status": receipt_status or {"SuccessValue": ""}, "logs": []}}
        ],
    }


def test_check_outcome():
    check_outcome(outcome(), "abc")
    with pytest.raises(SettlementError, match="another transaction"):
        check_outcome(outcome(), "xyz")
    with pytest.raises(SettlementError, match="failed"):
        check_outcome(outcome(status={"Failure": {"ActionError": {}}}), "abc")
    with pytest.raises(SettlementError, match="failed step"):
        check_outcome(outcome(receipt_status={"Failure": {"ActionError": {}}}), "abc")


class ScriptedRpc(NearRpc):
    """NearRpc with `call` answered from a script, to test signing and send/poll paths."""

    def __init__(self, send):
        super().__init__("http://unused")
        self.send = send
        self.sent = []

    def call(self, method, params):
        if method == "query":
            return {"nonce": 41, "block_hash": base58.b58encode(bytes(32)).decode()}
        if method == "send_tx":
            self.sent.append(base64.b64decode(params["signed_tx_base64"]))
            return self.send(self.sent[-1])
        if method == "tx":
            return outcome(params["tx_hash"])
        raise AssertionError(method)


def tx_hash_of(signed):
    return base58.b58encode(hashlib.sha256(signed[:-65]).digest()).decode()


def test_call_function_signs_a_valid_transaction():
    rpc = ScriptedRpc(lambda signed: outcome(tx_hash_of(signed)))
    key = load_signing_key(HUNTER_KEY)
    tx_hash, _ = rpc.call_function(ARSUNE, key, "intents.near", "add_public_key", {"a": 1}, gas=7)
    (signed,) = rpc.sent
    expected = function_call_tx(
        ARSUNE, bytes(key.verify_key), 42, "intents.near", bytes(32), "add_public_key",
        b'{"a":1}', 7, 0,
    )  # fmt: skip
    assert signed[:-65] == expected
    assert tx_hash == tx_hash_of(signed)
    key.verify_key.verify(hashlib.sha256(expected).digest(), signed[-64:])


def test_call_function_polls_after_timeout_and_reports_rejection(monkeypatch):
    def timeout(signed):
        raise NearRpcError({"name": "HANDLER_ERROR", "cause": {"name": "TIMEOUT_ERROR"}})

    key = load_signing_key(HUNTER_KEY)
    tx_hash, _ = ScriptedRpc(timeout).call_function(ARSUNE, key, "c.near", "m", {}, gas=1)
    assert tx_hash

    def rejected(signed):
        raise NearRpcError({"name": "HANDLER_ERROR", "cause": {"name": "INVALID_TRANSACTION"}})

    with pytest.raises(SettlementError, match="rejected") as caught:
        ScriptedRpc(rejected).call_function(ARSUNE, key, "c.near", "m", {}, gas=1)
    assert not isinstance(caught.value, OutcomeUnknown)

    def unreachable(signed):
        raise RpcUnavailable("down")

    class LostRpc(ScriptedRpc):
        def call(self, method, params):
            if method == "tx":
                raise RpcUnavailable("down")
            return super().call(method, params)

    lost = LostRpc(unreachable)
    monkeypatch.setattr(near_tx, "WAIT_SECONDS", 0.01)
    monkeypatch.setattr(near_tx.time, "sleep", lambda seconds: None)
    with pytest.raises(OutcomeUnknown) as unknown:
        lost.call_function(ARSUNE, key, "c.near", "m", {}, gas=1)
    assert unknown.value.tx_hash == tx_hash_of(lost.sent[0])
    assert len(lost.sent) > 1 and set(lost.sent) == {lost.sent[0]}  # same bytes re-broadcast
