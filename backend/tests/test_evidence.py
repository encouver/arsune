"""Public evidence bundles, their hashes, and the `app.verify` command."""

import hashlib
import json

from app import verify
from app.core.config import settings
from app.core.db import Session
from app.core.models import Claim
from app.main import app
from app.settlement.escrow import canonical_json, evidence_hash
from app.workers.tasks import scan
from fastapi.testclient import TestClient

# client and site are pytest fixtures; importing them shares the demo site setup.
from test_bounties import (  # noqa: F401
    broken_link,
    claim_view,
    client,
    open_bounty,
    site,
    submit,
)


def run(client, bounty, payload):  # noqa: F811 - the client fixture
    claim = submit(client, bounty, payload)
    scan.run(claim["job_id"])
    return claim_view(client, bounty, claim)


def evidence(client, claim):  # noqa: F811 - the client fixture
    response = client.get(f"/api/claims/{claim['id']}/evidence")
    assert response.status_code == 200, response.text
    return response


def use_test_api(monkeypatch):
    # verify talks HTTP; the test client is an httpx client for the same app.
    monkeypatch.setattr(verify.httpx, "Client", lambda base_url, timeout: TestClient(app))


def test_a_decided_claim_publishes_the_exact_bundle_that_its_hash_names(client, site):  # noqa: F811
    bounty = open_bounty(client, site)
    paid = run(client, bounty, broken_link(site, "hunter-a.testnet"))
    assert paid["status"] == "paid", paid
    assert paid["evidence_url"] == f"/api/claims/{paid['id']}/evidence"
    response = evidence(client, paid)
    assert response.headers["content-type"] == "application/json"
    assert response.headers["x-evidence-hash"] == paid["evidence_hash"]
    assert hashlib.sha256(response.content).hexdigest() == paid["evidence_hash"]
    bundle = response.json()
    assert canonical_json(bundle) == response.content
    assert evidence_hash(bundle) == paid["evidence_hash"]
    assert bundle["format"] == "arsune-evidence/1"
    assert bundle["claim"]["id"] == paid["id"] and bundle["claim"]["urls"] == paid["urls"]
    assert bundle["ruling"]["outcome"] == "proven" and bundle["ruling"]["final_fingerprint"]
    assert bundle["check"]["status"] == "confirmed"
    assert bundle["check"]["reason"] == paid["checker_reason"]
    observations = bundle["check"]["observations"]
    assert {o["url"] for o in observations} == set(paid["urls"])
    assert all(o["viewport"] == {"width": 1280, "height": 900} for o in observations)
    for observation in observations:
        assert "text" not in observation
        shot = observation["screenshot"]
        path = settings.evidence_dir / paid["job_id"] / shot["name"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == shot["sha256"]
    # No host path: only file names, served by the job's evidence route.
    assert str(settings.evidence_dir) not in response.text
    assert client.get(f"/api/claims/{paid['id']}").json() == paid

    rejected = run(client, bounty, broken_link(site, "hunter-b.testnet", "healthy"))
    assert evidence(client, rejected).json()["ruling"]["outcome"] == "rejected"

    duplicate = submit(client, bounty, broken_link(site, "hunter-c.testnet"))
    assert duplicate["evidence_hash"] is None  # known at arrival, decided by the worker
    assert client.get(f"/api/claims/{duplicate['id']}/evidence").status_code == 404
    scan.run(duplicate["job_id"])
    known = evidence(client, claim_view(client, bounty, duplicate)).json()
    assert known["ruling"]["outcome"] == "duplicate" and known["check"]["observations"] == []
    assert paid["id"] in known["check"]["reason"]

    assert client.get(f"/api/claims/{'0' * 8}-0000-4000-8000-{'0' * 12}").status_code == 404
    assert client.get("/api/claims/not-a-claim/evidence").status_code == 404


def test_verify_reproduces_a_ruling_and_catches_changed_or_false_records(
    client,  # noqa: F811
    site,  # noqa: F811
    monkeypatch,
    capsys,
):
    use_test_api(monkeypatch)
    bounty = open_bounty(client, site)
    paid = run(client, bounty, broken_link(site, "hunter-a.testnet"))
    rejected = run(client, bounty, broken_link(site, "hunter-b.testnet", "healthy"))

    assert verify.main(["http://testserver", paid["id"]]) == 0
    out = capsys.readouterr().out
    assert "MISMATCH" not in out and "Verdict: OK" in out
    assert "confirmed (a proven ruling needs confirmed)  MATCH" in out
    assert "none (settlement dry)" in out
    assert verify.main(["http://testserver", rejected["id"], "--no-browser"]) == 0
    assert "rejected (published rejected)  MATCH" in capsys.readouterr().out

    # A changed bundle no longer has the published hash.
    with Session.begin() as session:
        claim = session.get(Claim, paid["id"])
        original = claim.evidence_bundle
        claim.evidence_bundle = original.replace("HTTP 404", "HTTP 405", 1)
    assert verify.main(["http://testserver", paid["id"], "--no-browser"]) == 1
    assert "sha256 of the bundle:" in (out := capsys.readouterr().out)
    assert "MISMATCH" in out

    # A judge that publishes a false ruling with a matching hash is caught by the fresh check.
    false = json.loads(original)
    false["ruling"]["outcome"] = "rejected"
    data = canonical_json(false)
    with Session.begin() as session:
        claim = session.get(Claim, paid["id"])
        claim.evidence_bundle, claim.evidence_hash = data.decode(), evidence_hash(data)
        claim.outcome = "rejected"
    assert verify.main(["http://testserver", paid["id"]]) == 1
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if line.endswith("MISMATCH")] == [
        "Fresh browser check:        confirmed (a rejected ruling needs rejected)  MISMATCH"
    ]

    unknown = f"{'0' * 8}-0000-4000-8000-{'0' * 12}"
    assert verify.main(["http://testserver", unknown, "--no-browser"]) == 2
