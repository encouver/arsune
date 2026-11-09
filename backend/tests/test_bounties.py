"""Bounty proof, claim scope, real browser checks, and once-only settlement."""

import os
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import httpx
import pytest
from app.bounty import proof, settlement
from app.bounty.claims import ClaimError, fingerprint, scoped_claim
from app.bounty.worker import settle_claim
from app.browser.policy import origin
from app.core.config import settings
from app.core.db import Session
from app.core.models import (
    Attempt,
    Bounty,
    Claim,
    Event,
    Job,
    LedgerEntry,
    PendingAction,
    Record,
    utcnow,
)
from app.main import app
from app.settlement.base import SettlementError
from app.settlement.dry import DrySettlement
from app.workers import store
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

REWARD, BOND, FUND = 1_000_000, 100_000, 5_000_000
FEE = REWARD * 1000 // 10_000
CONFIRMED = {"status": "confirmed", "checker_reason": "Fresh target returned HTTP 404."}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def site():
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    with subprocess.Popen(
        [sys.executable, "-m", "app.demo"],
        env={**os.environ, "ARSUNE_DEMO_PORT": str(port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        try:
            for _ in range(80):
                try:
                    if httpx.get(url + "/faulty/", timeout=0.2).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Demo did not start.")
            yield url
        finally:
            process.terminate()
            process.wait(timeout=5)


@pytest.fixture
def client(site, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "demo_url", site)
    monkeypatch.setattr(settings, "evidence_dir", tmp_path / "evidence")
    test_client = TestClient(app)
    created = []
    original = test_client.post

    def post(url, *args, **kwargs):
        response = original(url, *args, **kwargs)
        if url == "/api/bounties" and response.status_code == 201:
            created.append(response.json()["id"])
        return response

    test_client.post = post
    yield test_client
    with Session.begin() as session:
        job_ids = session.scalars(
            select(Claim.job_id).where(Claim.bounty_id.in_(created), Claim.job_id.is_not(None))
        ).all()
        session.execute(delete(LedgerEntry).where(LedgerEntry.bounty_id.in_(created)))
        session.execute(delete(Claim).where(Claim.bounty_id.in_(created)))
        session.execute(delete(Bounty).where(Bounty.id.in_(created)))
        for model in (Record, Event, Attempt, PendingAction):
            session.execute(delete(model).where(model.job_id.in_(job_ids)))
        session.execute(delete(Job).where(Job.id.in_(job_ids)))


def open_bounty(client, site, fund=FUND):
    response = client.post(
        "/api/bounties",
        json={
            "url": site + "/faulty/",
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    )
    assert response.status_code == 201, response.text
    bounty = response.json()
    assert bounty["status"] == "pending_proof" and bounty["settlement_mode"] == "dry"
    httpx.post(site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"
    response = client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={"sender": "owner.testnet", "amount": fund, "signed_intent": {"dry": True}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def broken_link(site, hunter, target="missing"):
    source, destination = site + "/faulty/", f"{site}/faulty/{target}"
    return {
        "hunter_account": hunter,
        "claim_type": "broken_link",
        "urls": [source, destination],
        "details": {"source": source, "target": destination},
        "explanation": f"The {target} link is broken.",
        "reproduction_steps": ["Open the home page.", f"Follow the {target} link."],
        "bond_intent": {"dry": True},
    }


def submit(client, bounty, payload):
    response = client.post(f"/api/bounties/{bounty['id']}/claims", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def claim_view(client, bounty, claim):
    return client.get(f"/api/bounties/{bounty['id']}/claims/{claim['id']}").json()


def ledger(bounty_id, claim_id=None):
    with Session() as session:
        query = select(LedgerEntry).where(LedgerEntry.bounty_id == bounty_id)
        if claim_id:
            query = query.where(LedgerEntry.claim_id == claim_id)
        return session.scalars(query).all()


def seed_result(job_id, result=CONFIRMED):
    with Session.begin() as session:
        session.get(Job, job_id).checkpoint = {"result": {**result, "evidence": []}}


def expire_lease(job_id):
    with Session.begin() as session:
        session.get(Job, job_id).lease_until = utcnow() - timedelta(seconds=1)


class CountingSettlement(DrySettlement):
    def __init__(self):
        self.payments = []

    def pay(self, receiver, amount, memo):
        self.payments.append((receiver, amount, memo))
        return super().pay(receiver, amount, memo)


class Crash(BaseException):
    """Stops the worker like a killed process: ordinary exception handlers do not run."""


class ProofServer(BaseHTTPRequestHandler):
    responses: ClassVar[dict] = {}

    def do_GET(self):
        status, headers, body = self.responses.get(self.path, (404, {}, b"missing"))
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def proof_site(client, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProofServer)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setattr(settings, "demo_url", url)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield url
    server.shutdown()
    server.server_close()


def test_owner_proof_requires_the_exact_token_under_the_url_policy(client, proof_site, monkeypatch):
    bounty = client.post(
        "/api/bounties",
        json={
            "url": proof_site + "/shop",
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    ).json()
    token, path = bounty["proof_token"], "/.well-known/arsune-bounty.txt"
    assert bounty["proof_url"] == proof_site + path
    verify = f"/api/bounties/{bounty['id']}/verify"
    cases = [
        (404, {}, b"missing"),
        (200, {}, b"wrong-token"),
        (200, {}, token.encode() + b" " * 5000),
        (302, {"Location": "http://127.0.0.1:9/.well-known/arsune-bounty.txt"}, b""),
    ]
    for case in cases:
        ProofServer.responses = {path: case}
        response = client.post(verify)
        assert response.status_code == 409, (case, response.text)
        assert client.get(f"/api/bounties/{bounty['id']}").json()["status"] == "pending_proof"

    async def public(url, demo_origin=None):
        return url

    # Even a destination that passes the address policy must keep the bounty origin.
    original = proof.validate_target
    monkeypatch.setattr(proof, "validate_target", public)
    ProofServer.responses = {path: cases[-1]}
    response = client.post(verify)
    assert response.status_code == 409 and "another origin" in response.text
    monkeypatch.setattr(proof, "validate_target", original)
    response = client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={"sender": "owner.testnet", "amount": FUND, "signed_intent": {"dry": True}},
    )
    assert response.status_code == 409
    ProofServer.responses = {
        path: (302, {"Location": "/proof.txt"}, b""),
        "/proof.txt": (200, {}, f"  {token}\n".encode()),
    }
    response = client.post(verify)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"


def test_claims_are_limited_to_the_bounty_scope(client, site, monkeypatch):
    bounty = client.post(
        "/api/bounties",
        json={
            "url": site + "/faulty/",
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    ).json()
    claims = f"/api/bounties/{bounty['id']}/claims"
    payload = broken_link(site, "hunter-a.testnet")
    assert client.post(claims, json=payload).status_code == 409  # not verified yet
    bounty = open_bounty(client, site, fund=REWARD)  # funded below reward plus fee
    claims = f"/api/bounties/{bounty['id']}/claims"
    assert client.post(claims, json=payload).status_code == 409
    bounty = open_bounty(client, site)
    claims = f"/api/bounties/{bounty['id']}/claims"
    other = f"http://127.0.0.1:{free_port()}/faulty/missing"
    invalid = [
        {**payload, "claim_type": "xss"},
        {**payload, "urls": [site + "/faulty/", other]},
        {**payload, "details": {"source": site + "/faulty/", "target": other}},
        {**payload, "urls": [*payload["urls"], site + "/faulty/healthy"]},
        {**payload, "urls": [site + "/faulty/", site + "/faulty/missing?again=1"]},
        {**payload, "details": {**payload["details"], "nonce": "1"}},
        {**payload, "urls": ["file:///etc/passwd"]},
        {**payload, "hunter_account": "Not An Account"},
        {**payload, "reproduction_steps": []},
        {**payload, "explanation": "x" * 2001},
        {**payload, "bond_intent": {}},
    ]
    for body in invalid:
        response = client.post(claims, json=body)
        assert response.status_code == 422, (body, response.text)
    with Session() as session:
        assert not session.scalars(select(Claim).where(Claim.bounty_id == bounty["id"])).all()

    class FailingCollect(DrySettlement):
        def collect(self, signed_intent, *, sender, amount, memo):
            raise SettlementError("declined")

    monkeypatch.setattr(settlement, "get_settlement", FailingCollect)
    assert client.post(claims, json=payload).status_code == 402
    assert [row.kind for row in ledger(bounty["id"])] == ["fund"]
    monkeypatch.setattr(settlement, "get_settlement", DrySettlement)
    for _ in range(3):
        submit(client, bounty, payload)
    assert client.post(claims, json=payload).status_code == 429
    assert client.get("/api/bounties/not-a-bounty").status_code == 404
    assert client.get(f"/api/bounties/{bounty['id'].upper()}").status_code == 404
    first = open_bounty(client, site)
    claim = client.get(f"/api/bounties/{bounty['id']}").json()["claims"][0]
    assert client.get(f"/api/bounties/{first['id']}/claims/{claim['id']}").status_code == 404
    assert client.get(f"/api/bounties/{bounty['id']}/claims/{claim['id']}").status_code == 200
    jobs = client.get("/api/jobs?limit=50").json()["items"]
    assert claim["job_id"] not in {job["id"] for job in jobs}


async def test_fingerprint_names_the_fault_not_the_url_spelling(site, monkeypatch):
    monkeypatch.setattr(settings, "demo_url", site)
    plain = await scoped_claim(
        origin(site),
        "broken_link",
        [site + "/faulty/", site + "/faulty/missing"],
        {"source": site + "/faulty/", "target": site + "/faulty/missing"},
    )
    spelled = await scoped_claim(
        origin(site),
        "broken_link",
        [site + "/faulty/./missing#link", site + "/faulty/"],
        {"source": site + "/faulty/#top", "target": site + "/faulty/a/../missing"},
    )
    assert fingerprint("broken_link", *plain) == fingerprint("broken_link", *spelled)
    with pytest.raises(ClaimError, match="bounty site"):
        await scoped_claim(
            "http://127.0.0.1:1", "horizontal_overflow", [site + "/"], {"url": site + "/"}
        )


def test_real_browser_claims_are_paid_rejected_and_deduplicated(client, site):
    bounty = open_bounty(client, site)
    assert bounty["budget_amount"] == FUND
    first = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    assert first["status"] == "received" and first["settlement_mode"] == "dry"
    scan.run(first["job_id"])
    proven = claim_view(client, bounty, first)
    assert proven["status"] == "paid" and proven["outcome"] == "proven", proven
    assert "HTTP 404" in proven["checker_reason"]
    assert proven["evidence"] and all(item["screenshot_url"] for item in proven["evidence"])
    image = client.get(proven["evidence"][0]["screenshot_url"])
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    rows = {row["kind"]: row for row in proven["ledger"]}
    assert set(rows) == {"bond_in", "reward_out", "bond_refund", "fee"}
    assert rows["reward_out"]["amount"] == REWARD and rows["bond_refund"]["amount"] == BOND
    assert rows["reward_out"]["tx_id"] == rows["bond_refund"]["tx_id"]
    assert rows["reward_out"]["tx_id"].startswith("dry-")
    assert rows["reward_out"]["memo"] == f"arsune:reward:{first['id']}"
    assert rows["bond_in"]["memo"] == f"arsune:bond:{first['id']}"
    assert rows["fee"]["amount"] == FEE

    false = submit(client, bounty, broken_link(site, "hunter-b.testnet", "healthy"))
    scan.run(false["job_id"])
    rejected = claim_view(client, bounty, false)
    assert rejected["status"] == "rejected", rejected
    assert [row["kind"] for row in rejected["ledger"]] == ["bond_in", "bond_forfeit"]

    second = submit(client, bounty, broken_link(site, "hunter-c.testnet"))
    assert second["fingerprint"] == first["fingerprint"]
    scan.run(second["job_id"])
    duplicate = claim_view(client, bounty, second)
    assert duplicate["status"] == "duplicate" and duplicate["outcome"] == "duplicate"
    refund = next(row for row in duplicate["ledger"] if row["kind"] == "bond_refund")
    assert refund["amount"] == BOND and refund["tx_id"].startswith("dry-")
    assert not any(row["kind"] == "reward_out" for row in duplicate["ledger"])

    scan.run(first["job_id"])  # a repeated queue message does not pay again
    detail = client.get(f"/api/bounties/{bounty['id']}").json()
    assert detail["budget_amount"] == FUND - REWARD - FEE + BOND
    assert detail["status"] == "open" and detail["settlement_mode"] == "dry"
    assert sum(row["kind"] == "reward_out" for row in detail["ledger"]) == 1
    assert {row["settlement_mode"] for row in detail["ledger"]} == {"dry"}
    assert all(row["status"] == "completed" for row in detail["ledger"])
    assert [c["status"] for c in detail["claims"]] == ["paid", "rejected", "duplicate"]
    listed = client.get("/api/bounties").json()
    assert listed["settlement_mode"] == "dry"
    assert any(item["id"] == bounty["id"] for item in listed["items"])


def test_concurrent_equal_proven_claims_pay_only_once(client, site, monkeypatch):
    port = CountingSettlement()
    monkeypatch.setattr(settlement, "get_settlement", lambda: port)
    bounty = open_bounty(client, site)
    claims = [submit(client, bounty, broken_link(site, f"hunter-{n}.testnet")) for n in range(6)]
    generations = {c["job_id"]: store.claim(c["job_id"])["generation"] for c in claims}
    barrier = threading.Barrier(len(claims))
    errors = []

    def settle(job_id):
        try:
            barrier.wait()
            settle_claim(job_id, generations[job_id], {**CONFIRMED, "evidence": []})
        except Exception as exc:  # noqa: BLE001 - reported by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=settle, args=(c["job_id"],)) for c in claims]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not errors
    statuses = sorted(claim_view(client, bounty, c)["status"] for c in claims)
    assert statuses == ["duplicate"] * 5 + ["paid"]
    assert sum(amount == REWARD + BOND for _, amount, _ in port.payments) == 1
    assert sum(amount == BOND for _, amount, _ in port.payments) == 5
    rows = ledger(bounty["id"])
    assert sum(row.kind == "reward_out" for row in rows) == 1
    assert client.get(f"/api/bounties/{bounty['id']}").json()["budget_amount"] == (
        FUND - REWARD - FEE
    )


def test_crash_after_payment_row_never_pays_twice(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    claim = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    job_id = claim["job_id"]
    seed_result(job_id)

    class CrashAfterSend(CountingSettlement):
        def pay(self, receiver, amount, memo):
            super().pay(receiver, amount, memo)
            raise Crash

    crashing = CrashAfterSend()
    monkeypatch.setattr(settlement, "get_settlement", lambda: crashing)
    with pytest.raises(Crash):
        scan.run(job_id)
    assert len(crashing.payments) == 1
    [payment] = [row for row in ledger(bounty["id"], claim["id"]) if row.kind == "reward_out"]
    assert payment.tx_id is None and payment.status == "pending"

    expire_lease(job_id)
    retry = CountingSettlement()
    monkeypatch.setattr(settlement, "get_settlement", lambda: retry)
    scan.run(job_id)
    assert retry.payments == []
    view = claim_view(client, bounty, claim)
    assert view["status"] == "needs_review" and view["outcome"] == "proven"
    assert "no recorded result" in view["settlement_note"]
    assert sum(row["kind"] == "reward_out" for row in view["ledger"]) == 1
    assert view["job_status"] == "completed"


def test_crash_after_recorded_payment_completes_without_paying_again(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    claim = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    job_id = claim["job_id"]
    seed_result(job_id)
    port = CountingSettlement()
    monkeypatch.setattr(settlement, "get_settlement", lambda: port)
    finish = store.finish

    def crash_before_finish(*args, **kwargs):
        raise Crash

    monkeypatch.setattr(store, "finish", crash_before_finish)
    with pytest.raises(Crash):
        scan.run(job_id)
    monkeypatch.setattr(store, "finish", finish)
    assert claim_view(client, bounty, claim)["status"] == "paid"
    expire_lease(job_id)
    scan.run(job_id)
    assert len(port.payments) == 1
    view = claim_view(client, bounty, claim)
    assert view["status"] == "paid" and view["job_status"] == "completed"
    assert sum(row["kind"] == "reward_out" for row in view["ledger"]) == 1


def test_settlement_failure_and_failed_job_hold_the_claim_for_review(client, site, monkeypatch):
    bounty = open_bounty(client, site)

    class FailingPay(CountingSettlement):
        def pay(self, receiver, amount, memo):
            raise SettlementError("declined")

    monkeypatch.setattr(settlement, "get_settlement", FailingPay)
    unverified = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    seed_result(unverified["job_id"], {"status": "unverified", "checker_reason": "Unknown."})
    scan.run(unverified["job_id"])
    view = claim_view(client, bounty, unverified)
    assert view["status"] == "needs_review" and view["outcome"] == "unverified"
    assert [row["status"] for row in view["ledger"] if row["kind"] == "bond_refund"] == ["failed"]

    stopped = submit(client, bounty, broken_link(site, "hunter-b.testnet"))
    with Session.begin() as session:
        session.get(Job, stopped["job_id"]).deadline = utcnow() - timedelta(seconds=1)
    assert store.claim(stopped["job_id"]) is None
    view = claim_view(client, bounty, stopped)
    assert view["status"] == "needs_review" and view["job_status"] == "failed"
    assert [row["kind"] for row in view["ledger"]] == ["bond_in"]
