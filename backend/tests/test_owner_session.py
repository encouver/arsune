"""Sign in with NEAR: a NEP-413 signature of a one-time challenge gives an owner session.

The wallet side is written from the NEP-413 spec. The NEAR RPC is faked: view_access_key
answers from a dict, so no test reaches the network.
"""

import base64
import hashlib
import struct
import time

import base58
import pytest
from app.settlement.near_tx import NearRpcError
from app.v1.services import owner
from nacl.signing import SigningKey

# ruff: noqa: F811
from test_bounties import BOND, FUND, REWARD, client, site  # noqa: F401
from test_proof_reuse import pair, prove

ACCOUNT = "owner.testnet"
FULL = SigningKey.generate()
LIMITED = SigningKey.generate()
STRANGER = SigningKey.generate()


def key_text(key: SigningKey) -> str:
    return "ed25519:" + base58.b58encode(bytes(key.verify_key)).decode()


KEYS = {
    (ACCOUNT, key_text(FULL)): {"nonce": 1, "permission": "FullAccess"},
    (ACCOUNT, key_text(LIMITED)): {
        "nonce": 1,
        "permission": {"FunctionCall": {"receiver_id": "x.near", "method_names": []}},
    },
    ("other.testnet", key_text(STRANGER)): {"nonce": 1, "permission": "FullAccess"},
}


@pytest.fixture(autouse=True)
def fake_rpc(monkeypatch):
    def call(self, method, params):
        assert method == "query" and params["request_type"] == "view_access_key"
        found = KEYS.get((params["account_id"], params["public_key"]))
        if not found:
            raise NearRpcError({"cause": {"name": "UNKNOWN_ACCESS_KEY"}})
        return found

    monkeypatch.setattr("app.settlement.near_tx.NearRpc.call", call)


def wallet_sign(key: SigningKey, challenge: dict) -> dict:
    def string(value: str) -> bytes:
        data = value.encode()
        return struct.pack("<I", len(data)) + data

    nonce = base64.b64decode(challenge["nonce"])
    borsh = (
        struct.pack("<I", 2**31 + 413)
        + string(challenge["message"])
        + nonce
        + string(challenge["recipient"])
        + b"\0"
    )
    signature = key.sign(hashlib.sha256(borsh).digest()).signature
    return {
        "public_key": key_text(key),
        "signature": "ed25519:" + base58.b58encode(signature).decode(),
        "nonce": challenge["nonce"],
    }


def login(client, key=FULL, account=ACCOUNT):
    challenge = client.post("/api/owner/challenge").json()
    return client.post(
        "/api/owner/login", json={"account_id": account, **wallet_sign(key, challenge)}
    )


def bearer(session):
    return {"Authorization": f"Bearer {session}"}


def signed_in(client, key=FULL, account=ACCOUNT):
    response = login(client, key, account)
    assert response.status_code == 200, response.text
    return bearer(response.json()["session"])


def create(client, site, headers=None, owner_account=ACCOUNT, reuse=None):
    body = {
        "url": site + "/faulty/",
        "owner_account": owner_account,
        "reward_amount": REWARD,
        "bond_amount": BOND,
        "budget": FUND,
    }
    if reuse is not None:
        body["reuse_proof"] = reuse
    return client.post("/api/bounties", json=body, headers=headers or {})


def test_challenge_names_arsune(client):
    challenge = client.post("/api/owner/challenge").json()
    assert challenge["recipient"] == owner.RECIPIENT
    assert "Arsune" in challenge["message"]
    assert len(base64.b64decode(challenge["nonce"])) == 32


def test_full_access_key_signs_in(client):
    response = login(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_id"] == ACCOUNT
    assert body["expires_at"] > time.time() + 6 * 24 * 3600
    assert owner.session_account(f"Bearer {body['session']}") == ACCOUNT


def test_login_refusals(client):
    # A key of another account, a function-call key, and a key unknown to the account.
    assert login(client, STRANGER).status_code == 403
    assert login(client, LIMITED).status_code == 403
    assert login(client, SigningKey.generate()).status_code == 403

    # A signature of another message.
    challenge = client.post("/api/owner/challenge").json()
    forged = wallet_sign(FULL, {**challenge, "message": "something else"})
    response = client.post("/api/owner/login", json={"account_id": ACCOUNT, **forged})
    assert response.status_code == 401


def test_nonce_is_single_use(client):
    challenge = client.post("/api/owner/challenge").json()
    body = {"account_id": ACCOUNT, **wallet_sign(FULL, challenge)}
    assert client.post("/api/owner/login", json=body).status_code == 200
    assert client.post("/api/owner/login", json=body).status_code == 401


def test_expired_nonce_fails(client, monkeypatch):
    monkeypatch.setattr(owner, "CHALLENGE_TTL", 1)
    challenge = client.post("/api/owner/challenge").json()
    body = {"account_id": ACCOUNT, **wallet_sign(FULL, challenge)}
    time.sleep(1.5)
    assert client.post("/api/owner/login", json=body).status_code == 401


def test_bad_sessions_are_refused(client):
    token, _ = owner.new_session(ACCOUNT)
    body, mac = token.split(".")
    other_body = owner.new_session("other.testnet")[0].split(".")[0]
    old, _ = owner.new_session(ACCOUNT, now=time.time() - owner.SESSION_SECONDS - 5)
    for value in (f"{other_body}.{mac}", f"{body}.x", old, "junk", ""):
        assert owner.session_account(f"Bearer {value}") is None
        assert client.get("/api/owner/bounties", headers=bearer(value)).status_code == 401
    assert client.get("/api/owner/bounties").status_code == 401


def test_session_opens_owner_endpoints_of_its_account_only(client, site):
    bounty = create(client, site).json()
    url = f"/api/bounties/{bounty['id']}/access-test"
    assert client.get(url, headers=signed_in(client)).status_code == 200
    other = signed_in(client, STRANGER, "other.testnet")
    assert client.get(url, headers=other).status_code == 403
    body = {"login_url": site + "/login", "email": "a@x.com", "password": "b"}
    assert client.put(url, json=body, headers=other).status_code == 403
    response = client.put(url, json=body, headers=signed_in(client))
    assert response.status_code == 200 and response.json()["email"] == "a@x.com"
    # The owner key still works.
    assert client.get(url, headers={"X-Owner-Key": bounty["owner_key"]}).status_code == 200


def test_owner_bounties_lists_the_accounts_hunts(client, site):
    mine = create(client, site).json()
    theirs = create(client, site, owner_account="other.testnet").json()
    response = client.get("/api/owner/bounties", headers=signed_in(client))
    assert response.status_code == 200
    body = response.json()
    ids = [item["id"] for item in body["items"]]
    assert body["account_id"] == ACCOUNT
    assert mine["id"] in ids and theirs["id"] not in ids
    assert all(item["owner_account"] == ACCOUNT for item in body["items"])
    assert "owner_key" not in response.text and "proof_token" not in response.text


def test_signed_in_owner_must_be_the_session_account(client, site):
    headers = signed_in(client)
    assert create(client, site, headers, owner_account="other.testnet").status_code == 403
    assert create(client, site, bearer("junk")).status_code == 401
    assert create(client, site, headers).status_code == 201


def test_session_reuses_proof_in_another_browser(client, site):
    # Browser 1 proves the site. Browser 2 has no owner key, only a session.
    old = create(client, site).json()
    prove(old["id"])
    new = create(client, site, signed_in(client))
    assert new.status_code == 201, new.text
    assert new.json()["proof_token"] == old["proof_token"]


def test_newest_proven_bounty_of_the_account_wins(client, site):
    first = create(client, site).json()
    prove(first["id"])
    second = create(client, site).json()
    prove(second["id"])
    unproved = create(client, site).json()
    new = create(client, site, signed_in(client)).json()
    assert new["proof_token"] == second["proof_token"]
    assert new["proof_token"] != unproved["proof_token"]


def test_typed_owner_account_alone_does_not_reuse_proof(client, site):
    old = create(client, site).json()
    prove(old["id"])
    assert create(client, site).json()["proof_token"] != old["proof_token"]
    # Another account's session does not reuse it either.
    other = signed_in(client, STRANGER, "other.testnet")
    stranger = create(client, site, other, owner_account="other.testnet").json()
    assert stranger["proof_token"] != old["proof_token"]
    # The owner key path still works without a session.
    assert create(client, site, reuse=[pair(old)]).json()["proof_token"] == old["proof_token"]
