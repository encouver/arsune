"""The owner's security test: per-bounty markers, owner-only view, and the opening rule."""

from app.core.db import Session
from app.core.models import Bounty
from app.demo_shop import access_markers

# client and site are pytest fixtures; importing them shares the demo site setup.
# ruff: noqa: F811
from test_bounties import (  # noqa: F401
    BOND,
    FUND,
    REWARD,
    claim_payload,
    client,
    open_bounty,
    run_claim,
    site,
)


def create(client, site):
    response = client.post(
        "/api/bounties",
        json={
            "url": site + "/shop/",
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_public_views_show_test_account_a_but_never_a_marker(client, site):
    bounty = create(client, site)
    assert bounty["owner_key"]
    assert bounty["test_account"] == {
        "login_url": site + "/shop/login",
        "email": "alice@demo.shop",
        "password": "alice-demo",
    }
    texts = [
        client.get(f"/api/bounties/{bounty['id']}").text,
        client.get("/api/bounties").text,
        str(bounty),
    ]
    for value in access_markers():
        assert all(value not in text for text in texts)
    assert "owner_key" not in client.get(f"/api/bounties/{bounty['id']}").json()


def test_only_the_owner_reads_and_sets_the_security_test(client, site):
    bounty = create(client, site)
    url = f"/api/bounties/{bounty['id']}/access-test"
    assert client.get(url).status_code == 403
    assert client.get(url, headers={"X-Owner-Key": "wrong"}).status_code == 403
    owner = {"X-Owner-Key": bounty["owner_key"]}
    view = client.get(url, headers=owner).json()
    assert view["ready"] and [m["value"] for m in view["markers"]] == access_markers()
    assert view["markers"][0]["label"] == "other user's data"

    # A site that is not the demo shop: new random markers, and no login until the owner sets it.
    # The security test is optional now: verify does not ask for it.
    with Session.begin() as session:
        session.get(Bounty, bounty["id"]).access_test = None
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.json().get("detail") != "Set up the security test first (step 2)."
    for bad in (
        {"login_url": "/login", "email": "a", "password": "b"},
        {"login_url": "https://other.example/login", "email": "a", "password": "b"},
        {"login_url": site + "/login", "email": "", "password": "b"},
    ):
        assert client.put(url, json=bad, headers=owner).status_code == 422
    body = {"login_url": site + "/login", "email": "a@x.com", "password": "b"}
    assert client.put(url, json=body).status_code == 403
    view = client.put(url, json=body, headers=owner).json()
    assert view["ready"] and view["login_url"] == site + "/login"
    assert view["email"] == "a@x.com" and view["password"] == "b"
    values = [m["value"] for m in view["markers"]]
    assert len(set(values)) == 4 and all(
        len(v) >= 16 and "ARSUNE" not in v.upper() and "MARKER" not in v.upper() for v in values
    )
    assert values != access_markers()
    public = client.get(f"/api/bounties/{bounty['id']}").json()
    assert public["test_account"] == body
    # The rule passes; the proof check that follows is the next step.
    detail = client.post(f"/api/bounties/{bounty['id']}/verify").json()["detail"]
    assert detail != "Set up the security test first (step 2)."


def leak(site, hunter, path):
    steps = [{"method": "GET", "path": path, "body": None}]
    return claim_payload(hunter, "access_leak", [site + path], {"steps": steps})


def test_an_access_leak_is_proven_once_and_its_evidence_hides_the_markers(client, site):
    bounty = open_bounty(client, site)
    _, first = run_claim(client, bounty, leak(site, "hunter-a.testnet", "/shop/api/admin/stats"))
    assert first["outcome"] == "proven", first["checker_reason"]
    assert "marker 3 of 4" in first["checker_reason"]
    _, again = run_claim(
        client, bounty, leak(site, "hunter-b.testnet", "/shop/api/admin/stats?full=1")
    )
    assert again["outcome"] == "duplicate"
    _, harmless = run_claim(client, bounty, leak(site, "hunter-c.testnet", "/shop/api/orders"))
    assert harmless["outcome"] == "rejected"
    texts = [
        client.get(f"/api/claims/{first['id']}/evidence").text,
        client.get(f"/api/bounties/{bounty['id']}").text,
        str(first),
    ]
    assert "[MARKER]" in texts[0]
    assert not any(m in t for m in access_markers() for t in texts)
