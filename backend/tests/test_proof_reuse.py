"""A new bounty on a proven site reuses the token, only for the earlier bounty's owner key."""

from app.core.db import Session
from app.core.models import Bounty

# client and site are pytest fixtures; importing them shares the demo site setup.
# ruff: noqa: F811
from test_bounties import BOND, FUND, REWARD, client, site  # noqa: F401


def create(client, site, reuse=None):
    body = {
        "url": site + "/faulty/",
        "owner_account": "owner.testnet",
        "reward_amount": REWARD,
        "bond_amount": BOND,
        "budget": FUND,
    }
    if reuse is not None:
        body["reuse_proof"] = reuse
    response = client.post("/api/bounties", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def prove(bounty_id, **changes):
    with Session.begin() as session:
        row = session.get(Bounty, bounty_id)
        row.status = changes.pop("status", "open")
        for name, value in changes.items():
            setattr(row, name, value)


def pair(bounty):
    return {"bounty_id": bounty["id"], "owner_key": bounty["owner_key"]}


def test_valid_key_reuses_token(client, site):
    old = create(client, site)
    prove(old["id"])
    new = create(client, site, [pair(old)])
    assert new["proof_token"] == old["proof_token"]
    assert new["status"] == "pending_proof"
    assert new["owner_key"] != old["owner_key"]


def test_newest_matching_bounty_wins(client, site):
    first = create(client, site)
    prove(first["id"])
    second = create(client, site)
    prove(second["id"])
    new = create(client, site, [pair(first), pair(second)])
    assert new["proof_token"] == second["proof_token"]


def test_no_reuse_when_not_qualified(client, site):
    old = create(client, site)
    prove(old["id"])
    wrong_key = create(client, site, [{"bounty_id": old["id"], "owner_key": "x" * 24}])
    assert wrong_key["proof_token"] != old["proof_token"]

    prove(old["id"], origin="http://other.example")
    other_origin = create(client, site, [pair(old)])
    assert other_origin["proof_token"] != old["proof_token"]

    unproved = create(client, site)
    never = create(client, site, [pair(unproved)])
    assert never["proof_token"] != unproved["proof_token"]


def test_unknown_bounty_is_ignored(client, site):
    new = create(client, site, [{"bounty_id": "missing", "owner_key": "k"}])
    assert new["status"] == "pending_proof"
