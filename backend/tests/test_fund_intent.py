"""POST /api/bounties/{id}/fund-intent: the payload a NEAR wallet signs with NEP-413 signMessage.

The wallet side is written here from the NEP-413 spec, not with Arsune's signing helpers: the
Borsh payload, sha256, an ed25519 signature returned in base64, and the base64 to base58 step
the web page does before it calls /fund.
"""

import base64
import hashlib
import json
import struct
from uuid import uuid4

import base58
import pytest
from app.bounty import settlement
from app.core.config import settings
from app.settlement import near_intents
from app.settlement.base import SettlementError
from nacl.signing import SigningKey

# ruff: noqa: F811
from test_bounties import FUND, client, open_bounty, site  # noqa: F401

OWNER = "owner.testnet"
WALLET_KEY = SigningKey.generate()


def wallet_sign_message(message: str, nonce: bytes, recipient: str) -> dict:
    """What a NEP-413 wallet returns: {accountId, publicKey 'ed25519:<base58>', base64 sig}."""

    def string(value: str) -> bytes:
        data = value.encode()
        return struct.pack("<I", len(data)) + data

    assert len(nonce) == 32
    # Borsh {tag: u32 = 2^31 + 413, message, nonce: [u8; 32], recipient, callbackUrl: None}
    borsh = struct.pack("<I", 2**31 + 413) + string(message) + nonce + string(recipient) + b"\0"
    signature = WALLET_KEY.sign(hashlib.sha256(borsh).digest()).signature
    public_key = "ed25519:" + base58.b58encode(bytes(WALLET_KEY.verify_key)).decode()
    return {
        "accountId": OWNER,
        "publicKey": public_key,
        "signature": base64.b64encode(signature).decode(),
    }


def page_signed_intent(prepared: dict) -> dict:
    """The page's step: wallet answer -> the signed_intent body that /fund accepts."""
    signed = wallet_sign_message(
        prepared["message"], base64.b64decode(prepared["nonce"]), prepared["recipient"]
    )
    signature = "ed25519:" + base58.b58encode(base64.b64decode(signed["signature"])).decode()
    return {
        "standard": "nep413",
        "payload": {
            "message": prepared["message"],
            "nonce": prepared["nonce"],
            "recipient": prepared["recipient"],
        },
        "public_key": signed["publicKey"],
        "signature": signature,
    }


def prepare(client, bounty_id, fund_id, amount=FUND, sender=OWNER):
    return client.post(
        f"/api/bounties/{bounty_id}/fund-intent",
        json={"fund_id": fund_id, "sender": sender, "amount": amount},
    )


def test_wallet_signed_fund_intent_funds_the_bounty(client, site):
    bounty = open_bounty(client, site)
    before = bounty["budget_amount"]
    fund_id = str(uuid4())
    response = prepare(client, bounty["id"], fund_id)
    assert response.status_code == 200, response.text
    prepared = response.json()
    assert set(prepared) == {"message", "nonce", "recipient", "memo", "deadline"}
    assert prepared["recipient"] == settings.intents_contract
    assert prepared["memo"] == f"arsune:fund:{fund_id}"
    assert len(base64.b64decode(prepared["nonce"])) == 32
    message = json.loads(prepared["message"])
    assert prepared["message"] == json.dumps(message, separators=(",", ":"), sort_keys=True)
    assert message == {
        "deadline": prepared["deadline"],
        "signer_id": OWNER,
        "intents": [
            {
                "intent": "transfer",
                "receiver_id": settlement.arsune_account(),
                "tokens": {settings.bounty_token: str(FUND)},
                "memo": prepared["memo"],
            }
        ],
    }

    response = client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={
            "fund_id": fund_id,
            "sender": OWNER,
            "amount": FUND,
            "signed_intent": page_signed_intent(prepared),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["budget_amount"] == before + FUND
    # The fund_id is spent: preparing it again is refused.
    assert prepare(client, bounty["id"], fund_id).status_code == 409


def test_a_signature_for_another_amount_or_sender_is_refused(client, site):
    bounty = open_bounty(client, site)
    fund_id = str(uuid4())
    prepared = prepare(client, bounty["id"], fund_id).json()
    signed = page_signed_intent(prepared)
    for sender, amount in (("other.testnet", FUND), (OWNER, FUND + 1)):
        response = client.post(
            f"/api/bounties/{bounty['id']}/fund",
            json={"fund_id": fund_id, "sender": sender, "amount": amount, "signed_intent": signed},
        )
        assert response.status_code == 422, response.text
    tampered = json.loads(json.dumps(signed))
    tampered["payload"]["message"] = prepared["message"].replace(str(FUND), str(FUND * 2))
    response = client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={"fund_id": fund_id, "sender": OWNER, "amount": FUND * 2, "signed_intent": tampered},
    )
    assert response.status_code == 422
    assert "signature does not match" in response.text


def test_fund_intent_validates_like_fund(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    assert prepare(client, bounty["id"], str(uuid4()), sender="Bad Account").status_code == 422
    assert prepare(client, bounty["id"], str(uuid4()), sender="arsune.dry").status_code == 422
    assert prepare(client, bounty["id"], str(uuid4()), amount=0).status_code == 422
    missing = prepare(client, "00000000-0000-4000-8000-000000000000", str(uuid4()))
    assert missing.status_code == 404
    monkeypatch.setattr(settings, "settlement_mode", "near_intents")
    over = prepare(client, bounty["id"], str(uuid4()), amount=settings.max_payment_amount + 1)
    assert over.status_code == 422
    assert "at most" in over.text


@pytest.mark.parametrize("mode", ["near_intents", "escrow"])
def test_prepared_payload_passes_the_mode_check(monkeypatch, mode):
    """Both real modes: a wallet signature over the prepared payload passes check_transfer."""
    monkeypatch.setattr(settings, "settlement_mode", mode)
    monkeypatch.setattr(settings, "near_account_id", "arsune-test.near")
    monkeypatch.setattr(settings, "escrow_contract", "escrow.arsune-test.near")
    monkeypatch.setattr(settlement, "current_salt", lambda: "a1b2c3d4")
    bounty_id = str(uuid4())
    msg = settlement.fund_msg(bounty_id)
    assert (msg is None) == (mode == "near_intents")
    memo = f"arsune:fund:{uuid4()}"
    prepared = settlement.transfer_payload(sender=OWNER, amount=FUND, memo=memo, msg=msg)
    nonce = base64.b64decode(prepared["nonce"])
    assert nonce[:4] == bytes.fromhex("5628f6c6") and nonce[5:9] == bytes.fromhex("a1b2c3d4")
    intent = json.loads(prepared["message"])["intents"][0]
    if mode == "escrow":
        assert intent["receiver_id"] == settings.escrow_contract
        assert intent["msg"] == json.dumps({"fund": bounty_id}, separators=(",", ":"))
    else:
        assert intent["receiver_id"] == "arsune-test.near" and "msg" not in intent
    signed = page_signed_intent(prepared)
    settlement.check_transfer(signed, sender=OWNER, amount=FUND, memo=memo, msg=msg)
    with pytest.raises(SettlementError):
        settlement.check_transfer(signed, sender=OWNER, amount=FUND, memo=memo + "x", msg=msg)


def test_fund_refuses_a_key_unknown_to_near_intents_without_a_ledger_row(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    monkeypatch.setattr(settings, "settlement_mode", "near_intents")
    monkeypatch.setattr(settings, "near_account_id", "arsune-test.near")
    monkeypatch.setattr(settlement, "current_salt", lambda: "a1b2c3d4")
    registered = {"value": False}
    asked = []

    def has_public_key(account_id, public_key, **_):
        asked.append((account_id, public_key))
        return registered["value"]

    monkeypatch.setattr(near_intents, "has_public_key", has_public_key)
    monkeypatch.setattr(
        settlement,
        "collect",
        lambda *a, **k: "tx-collected",
    )
    fund_id = str(uuid4())
    prepared = prepare(client, bounty["id"], fund_id, amount=1000).json()
    body = {
        "fund_id": fund_id,
        "sender": OWNER,
        "amount": 1000,
        "signed_intent": page_signed_intent(prepared),
    }
    response = client.post(f"/api/bounties/{bounty['id']}/fund", json=body)
    assert response.status_code == 422
    assert "Click 'Allow this wallet key' first." in response.text
    assert asked == [(OWNER, body["signed_intent"]["public_key"])]
    # No failed ledger row: the same fund_id is still free, and works once the key is added.
    assert prepare(client, bounty["id"], fund_id, amount=1000).status_code == 200
    registered["value"] = True
    assert client.post(f"/api/bounties/{bounty['id']}/fund", json=body).status_code == 200
