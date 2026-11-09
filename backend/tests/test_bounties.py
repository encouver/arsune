"""Bounty proof, claim scope, real browser checks, and once-only settlement."""

import copy
import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from uuid import uuid4

import httpx
import pytest
from app.bounty import proof, settlement, worker
from app.bounty.claims import ClaimError, fingerprint, scoped_claim
from app.bounty.recovery import recover_stuck_claims
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
from app.settlement import near_intents
from app.settlement.base import SettlementError
from app.settlement.dry import DRY_SALT, DrySettlement
from app.settlement.near_tx import OutcomeUnknown, generate_key
from app.v1.services import bounties as bounty_service
from app.workers import store
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, select

REWARD, BOND, FUND = 1_000_000, 100_000, 5_000_000
FEE = REWARD * 1000 // 10_000
CONFIRMED = {"status": "confirmed", "checker_reason": "Fresh target returned HTTP 404."}
# Signer keys are not checked against accounts before simulation, so one test key signs for all.
KEY, _ = generate_key()


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
    # Tests never use the local NEAR account or a real RPC endpoint.
    monkeypatch.setattr(settings, "settlement_mode", "dry")
    monkeypatch.setattr(settings, "near_account_id", "")
    monkeypatch.setattr(settings, "near_private_key", SecretStr(""))
    monkeypatch.setattr(settings, "near_rpc_url", "http://127.0.0.1:9/")
    monkeypatch.setattr(settlement, "_salt", {"value": None, "at": 0.0})
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


def open_bounty(client, site, amount=FUND):
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
    assert bounty["status"] == "pending_proof"
    assert bounty["settlement_mode"] == settings.settlement_mode
    httpx.post(site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "open"
    response = fund(client, bounty, amount)
    assert response.status_code == 200, response.text
    return response.json()


def signed(sender, amount, memo, receiver=None, deadline=None):
    return near_intents.sign_transfer(
        sender,
        KEY,
        receiver or settlement.arsune_account(),
        settings.bounty_token,
        amount,
        memo,
        deadline or datetime.now(UTC) + timedelta(minutes=5),
        salt=bytes.fromhex(settlement.current_salt()),
    )


def fund(client, bounty, amount=FUND, fund_id=None, intent=None):
    fund_id = fund_id or str(uuid4())
    return client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={
            "fund_id": fund_id,
            "sender": "owner.testnet",
            "amount": amount,
            "signed_intent": intent or signed("owner.testnet", amount, f"arsune:fund:{fund_id}"),
        },
    )


def claim_payload(hunter, claim_type, urls, details, scout=None, claim_id=None, bond=BOND):
    claim_id = claim_id or str(uuid4())
    payload = {
        "claim_id": claim_id,
        "hunter_account": hunter,
        "claim_type": claim_type,
        "urls": urls,
        "details": details,
        "explanation": f"The page has a {claim_type} fault.",
        "reproduction_steps": ["Open the named pages.", "Compare the result."],
        "bond_intent": signed(hunter, bond, f"arsune:bond:{claim_id}"),
    }
    if scout:
        payload["scout"] = scout
    return payload


def broken_link(site, hunter, target="missing", **kwargs):
    source, destination = site + "/faulty/", f"{site}/faulty/{target}"
    details = {"source": source, "target": destination}
    return claim_payload(hunter, "broken_link", [source, destination], details, **kwargs)


def overflow(site, hunter, **kwargs):
    url = site + "/faulty/"
    return claim_payload(hunter, "horizontal_overflow", [url], {"url": url}, **kwargs)


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
        super().__init__()
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
    assert fund(client, bounty).status_code == 409
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
    bounty = open_bounty(client, site, amount=REWARD)  # funded below reward plus fee
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
        submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    limited = client.post(claims, json=broken_link(site, "hunter-a.testnet"))
    assert limited.status_code == 429
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
    assert second["status"] == "duplicate"  # known at arrival
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
    assert all(row["explorer_url"] is None for row in detail["ledger"])
    hunters = {row["account"]: row for row in detail["hunters"]}
    assert hunters["hunter-a.testnet"] | {"account": None} == {
        "account": None,
        "claims": 1,
        "proven": 1,
        "rejected": 0,
        "duplicate": 0,
        "unverified": 0,
        "unfunded": 0,
        "pending": 0,
        "earned": REWARD,
        "bonds_lost": 0,
        "scout_earned": 0,
    }
    assert hunters["hunter-b.testnet"]["rejected"] == 1
    assert hunters["hunter-b.testnet"]["bonds_lost"] == BOND
    assert hunters["hunter-c.testnet"]["duplicate"] == 1
    assert hunters["hunter-c.testnet"]["earned"] == 0
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


def test_settlement_info_in_dry_mode(client):
    assert client.get("/api/settlement").json() == {
        "mode": "dry",
        "arsune_account": "arsune.dry",
        "token": settings.bounty_token,
        "intents_contract": settings.intents_contract,
        "salt": DRY_SALT,
    }
    assert isinstance(settlement.get_settlement(), DrySettlement)


def test_bond_must_be_a_signed_transfer_for_this_claim(client, site):
    bounty = open_bounty(client, site)
    claims = f"/api/bounties/{bounty['id']}/claims"
    hunter = "hunter-a.testnet"
    good = broken_link(site, hunter)
    memo = f"arsune:bond:{good['claim_id']}"
    tampered = copy.deepcopy(good["bond_intent"])
    message = tampered["payload"]["message"]
    tampered["payload"]["message"] = message.replace(f'"{BOND}"', f'"{BOND + 1}"')
    assert tampered["payload"]["message"] != message
    wrong = {
        "memo": signed(hunter, BOND, f"arsune:bond:{uuid4()}"),
        "amount": signed(hunter, BOND - 1, memo),
        "receiver": signed(hunter, BOND, memo, receiver="someone-else.testnet"),
        "sender": signed("hunter-z.testnet", BOND, memo),
        "signature": tampered,
        "deadline": signed(hunter, BOND, memo, deadline=datetime.now(UTC) - timedelta(seconds=1)),
        "not signed": {"dry": True},
    }
    for name, intent in wrong.items():
        response = client.post(claims, json={**good, "bond_intent": intent})
        assert response.status_code == 422, (name, response.text)
    for claim_id in ("not-a-uuid", str(uuid4()).upper(), "6ba7b810-9dad-11d1-80b4-00c04fd430c8"):
        response = client.post(claims, json={**good, "claim_id": claim_id})
        assert response.status_code == 422, claim_id
    with Session() as session:
        assert not session.scalars(select(Claim).where(Claim.bounty_id == bounty["id"])).all()
    assert [row.kind for row in ledger(bounty["id"])] == ["fund"]

    accepted = submit(client, bounty, good)
    assert accepted["claim_id"] == good["claim_id"] and accepted["status"] == "received"
    [bond] = [row for row in accepted["ledger"] if row["kind"] == "bond_in"]
    assert bond["memo"] == memo and bond["tx_id"].startswith("dry-")
    reused = broken_link(site, "hunter-b.testnet", claim_id=good["claim_id"])
    assert client.post(claims, json=reused).status_code == 409
    other = open_bounty(client, site)
    assert client.post(f"/api/bounties/{other['id']}/claims", json=reused).status_code == 409
    fund_id = str(uuid4())
    assert fund(client, bounty, 1_000, fund_id=fund_id).status_code == 200
    assert fund(client, bounty, 1_000, fund_id=fund_id).status_code == 409
    bad_fund = signed("owner.testnet", 1_001, f"arsune:fund:{fund_id}")
    assert fund(client, bounty, 1_000, intent=bad_fund).status_code == 422
    scout_self = {"account": "arsune.dry", "share_bps": 100, "tip_id": "t"}
    assert (
        client.post(claims, json=broken_link(site, "x.testnet", scout=scout_self)).status_code
        == 422
    )


def test_scout_share_is_paid_with_its_own_ledger_row(client, site, monkeypatch):
    port = CountingSettlement()
    monkeypatch.setattr(settlement, "get_settlement", lambda: port)
    bounty = open_bounty(client, site)
    scout = {"account": "scout-1.testnet", "share_bps": 2500, "tip_id": "tip-42"}
    claim = submit(client, bounty, broken_link(site, "hunter-a.testnet", scout=scout))
    assert claim["scout"] == scout
    seed_result(claim["job_id"])
    scan.run(claim["job_id"])
    share = REWARD * 2500 // 10_000
    assert port.payments == [
        ("hunter-a.testnet", REWARD - share + BOND, f"arsune:reward:{claim['id']}"),
        ("scout-1.testnet", share, f"arsune:scout:{claim['id']}"),
    ]
    view = claim_view(client, bounty, claim)
    assert view["status"] == "paid", view
    rows = {row["kind"]: row for row in view["ledger"]}
    assert rows["reward_out"]["amount"] == REWARD - share
    assert rows["bond_refund"]["tx_id"] == rows["reward_out"]["tx_id"]
    assert rows["scout_share"]["account"] == "scout-1.testnet"
    assert rows["scout_share"]["amount"] == share
    assert rows["scout_share"]["tx_id"] not in (None, rows["reward_out"]["tx_id"])
    assert rows["fee"]["amount"] == FEE
    detail = client.get(f"/api/bounties/{bounty['id']}").json()
    assert detail["budget_amount"] == FUND - REWARD - FEE
    hunters = {row["account"]: row for row in detail["hunters"]}
    assert hunters["hunter-a.testnet"]["earned"] == REWARD - share
    assert hunters["scout-1.testnet"]["scout_earned"] == share
    assert hunters["scout-1.testnet"]["claims"] == 0

    class CrashOnScout(CountingSettlement):
        def pay(self, receiver, amount, memo):
            tx_id = super().pay(receiver, amount, memo)
            if memo.startswith("arsune:scout:"):
                raise Crash
            return tx_id

    crashing = CrashOnScout()
    monkeypatch.setattr(settlement, "get_settlement", lambda: crashing)
    second = submit(client, bounty, overflow(site, "hunter-b.testnet", scout=scout))
    seed_result(second["job_id"])
    with pytest.raises(Crash):
        scan.run(second["job_id"])
    assert [memo.split(":")[1] for _, _, memo in crashing.payments] == ["reward", "scout"]
    expire_lease(second["job_id"])
    retry = CountingSettlement()
    monkeypatch.setattr(settlement, "get_settlement", lambda: retry)
    scan.run(second["job_id"])
    assert retry.payments == []
    view = claim_view(client, bounty, second)
    assert view["status"] == "needs_review" and view["outcome"] == "proven"
    rows = {row["kind"]: row for row in view["ledger"]}
    assert rows["reward_out"]["tx_id"] and rows["scout_share"]["tx_id"] is None


def test_known_fault_is_a_duplicate_without_a_browser(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    early = submit(client, bounty, broken_link(site, "hunter-b.testnet"))
    first = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    seed_result(first["job_id"])
    scan.run(first["job_id"])
    assert claim_view(client, bounty, first)["status"] == "paid"

    async def no_browser(*args, **kwargs):
        raise AssertionError("A known duplicate opened a browser.")

    monkeypatch.setattr(worker, "browser_check", no_browser)
    late = submit(client, bounty, broken_link(site, "hunter-c.testnet"))
    assert late["status"] == "duplicate" and first["id"] in late["checker_reason"]
    for claim in (early, late):
        scan.run(claim["job_id"])
        view = claim_view(client, bounty, claim)
        assert view["status"] == "duplicate" and view["outcome"] == "duplicate", view
        assert first["id"] in view["checker_reason"]
        [refund] = [row for row in view["ledger"] if row["kind"] == "bond_refund"]
        assert refund["amount"] == BOND and refund["tx_id"]
        assert view["evidence"] == []


def test_interrupted_intake_is_recovered_by_the_coordinator(client, site, monkeypatch):
    bounty = open_bounty(client, site)
    claims = f"/api/bounties/{bounty['id']}/claims"

    def stop(*args, **kwargs):
        raise RuntimeError("The API process stopped.")

    monkeypatch.setattr(bounty_service, "start_claim", stop)
    recorded = broken_link(site, "hunter-a.testnet")
    with pytest.raises(RuntimeError):
        client.post(claims, json=recorded)
    monkeypatch.setattr(settlement, "collect", stop)
    unknown = broken_link(site, "hunter-b.testnet")
    with pytest.raises(RuntimeError):
        client.post(claims, json=unknown)

    def state(claim_id):
        with Session() as session:
            claim = session.get(Claim, claim_id)
            [bond] = ledger(bounty["id"], claim_id)
            return claim.status, claim.job_id, bond.status, bond.tx_id

    assert state(recorded["claim_id"])[:3] == ("received", None, "completed")
    assert state(unknown["claim_id"]) == ("received", None, "pending", None)
    recover_stuck_claims()
    assert state(unknown["claim_id"])[0] == "received"  # still inside the intake timeout
    with Session.begin() as session:
        for claim_id in (recorded["claim_id"], unknown["claim_id"]):
            session.get(Claim, claim_id).created_at = utcnow() - timedelta(minutes=10)
    recover_stuck_claims()
    recover_stuck_claims()
    status, job_id, _, _ = state(recorded["claim_id"])
    assert status == "received" and job_id
    with Session() as session:
        assert session.get(PendingAction, f"{job_id}:scan")
    status, job_id, bond_status, _ = state(unknown["claim_id"])
    assert (status, job_id, bond_status) == ("needs_review", None, "unknown")
    seed_result(state(recorded["claim_id"])[1])
    scan.run(state(recorded["claim_id"])[1])
    assert claim_view(client, bounty, {"id": recorded["claim_id"]})["status"] == "paid"


class FakeRpc:
    """Answers the Verifier calls in memory. It never opens a network connection."""

    sent: ClassVar[list] = []
    unknown_memos: ClassVar[tuple] = ()

    def __init__(self, url=None, timeout=30.0):
        pass

    def view(self, contract, method, args):
        if method == "current_salt":
            return "252812b3"
        if method == "simulate_intents":
            return {"intents_executed": self.executed(args["signed"])}
        raise AssertionError(method)

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        [intent] = args["signed"]
        memo = json.loads(intent["payload"]["message"])["intents"][0]["memo"]
        FakeRpc.sent.append(memo)
        if memo.startswith(FakeRpc.unknown_memos):
            raise OutcomeUnknown("no final outcome from NEAR RPC", "UnknownHash")
        event = {"standard": "dip4", "event": "intents_executed", "data": self.executed([intent])}
        logs = ["EVENT_JSON:" + json.dumps(event)]
        return f"NearTx{len(FakeRpc.sent)}", {"receipts_outcome": [{"outcome": {"logs": logs}}]}

    @staticmethod
    def executed(signed_list):
        return [
            {
                "account_id": json.loads(s["payload"]["message"])["signer_id"],
                "nonce": s["payload"]["nonce"],
            }
            for s in signed_list
        ]


def test_near_intents_mode_uses_the_real_settlement_with_a_fake_rpc(client, site, monkeypatch):
    arsune_key, _ = generate_key()
    monkeypatch.setattr(near_intents, "NearRpc", FakeRpc)
    monkeypatch.setattr(FakeRpc, "sent", [])
    monkeypatch.setattr(settings, "settlement_mode", "near_intents")
    monkeypatch.setattr(settings, "near_account_id", "arsune-test.testnet")
    monkeypatch.setattr(settings, "near_private_key", SecretStr(arsune_key))
    monkeypatch.setattr(settings, "max_payment_amount", REWARD)
    info = client.get("/api/settlement").json()
    assert info == {
        "mode": "near_intents",
        "arsune_account": "arsune-test.testnet",
        "token": settings.bounty_token,
        "intents_contract": settings.intents_contract,
        "salt": "252812b3",
    }
    assert isinstance(settlement.get_settlement(), near_intents.NearIntentsSettlement)
    too_large = {
        "url": site + "/faulty/",
        "owner_account": "owner.testnet",
        "reward_amount": REWARD,
        "bond_amount": BOND,
        "budget": FUND,
    }
    assert client.post("/api/bounties", json=too_large).status_code == 422
    monkeypatch.setattr(settings, "max_payment_amount", FUND)
    bounty = open_bounty(client, site)
    assert bounty["settlement_mode"] == "near_intents"
    claim = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    seed_result(claim["job_id"])
    scan.run(claim["job_id"])
    view = claim_view(client, bounty, claim)
    assert view["status"] == "paid", view
    rows = {row["kind"]: row for row in view["ledger"]}
    assert rows["reward_out"]["tx_id"].startswith("NearTx")
    assert rows["reward_out"]["explorer_url"] == (
        "https://nearblocks.io/txns/" + rows["reward_out"]["tx_id"]
    )
    assert rows["fee"]["explorer_url"] is None and rows["fee"]["account"] == "arsune-test.testnet"
    assert [memo.rsplit(":", 1)[0] for memo in FakeRpc.sent] == [
        "arsune:fund",
        "arsune:bond",
        "arsune:reward",
    ]
    assert FakeRpc.sent[1:] == [f"arsune:bond:{claim['id']}", f"arsune:reward:{claim['id']}"]

    monkeypatch.setattr(FakeRpc, "unknown_memos", ("arsune:reward:",))
    second = submit(client, bounty, overflow(site, "hunter-b.testnet"))
    seed_result(second["job_id"])
    scan.run(second["job_id"])
    view = claim_view(client, bounty, second)
    assert view["status"] == "needs_review" and "UnknownHash" in view["settlement_note"]
    [payment] = [row for row in view["ledger"] if row["kind"] == "reward_out"]
    assert payment["status"] == "unknown" and payment["tx_id"] is None
    sent = len(FakeRpc.sent)
    expire_lease(second["job_id"])
    with Session.begin() as session:
        session.get(Job, second["job_id"]).status = "running"
    scan.run(second["job_id"])
    assert len(FakeRpc.sent) == sent


def page_overflow(site, hunter, path):
    url = site + path
    return claim_payload(hunter, "horizontal_overflow", [url], {"url": url})


def run_claim(client, bounty, payload):
    claim = submit(client, bounty, payload)
    scan.run(claim["job_id"])
    return claim, claim_view(client, bounty, claim)


def paid_rewards(bounty_id):
    return [row for row in ledger(bounty_id) if row.kind == "reward_out"]


@pytest.mark.parametrize("alias_first", [True, False])
def test_redirect_alias_and_final_page_are_one_overflow_fault(client, site, alias_first):
    bounty = open_bounty(client, site)
    alias, final = "/shop/old-pricing", "/shop/pricing"
    first_path, second_path = (alias, final) if alias_first else (final, alias)
    first, view = run_claim(client, bounty, page_overflow(site, "hunter-a.testnet", first_path))
    assert view["status"] == "paid" and view["outcome"] == "proven", view
    second = submit(client, bounty, page_overflow(site, "hunter-b.testnet", second_path))
    # Only the exact spelling of an already-held final page is known at arrival.
    assert second["status"] == ("duplicate" if alias_first else "received")
    scan.run(second["job_id"])
    duplicate = claim_view(client, bounty, second)
    assert duplicate["status"] == "duplicate" and duplicate["outcome"] == "duplicate", duplicate
    assert first["id"] in duplicate["checker_reason"]
    assert [row["kind"] for row in duplicate["ledger"]] == ["bond_in", "bond_refund"]
    assert len(paid_rewards(bounty["id"])) == 1
    with Session() as session:
        held = session.get(Claim, first["id"]).final_fingerprint
        assert held == fingerprint("horizontal_overflow", [site + final], {"url": site + final})


def test_redirecting_source_page_is_the_same_broken_link_fault(client, site):
    from app.demo_shop import BROKEN_LINKS, info_path

    source, target = next((s, t) for s, t, *_ in BROKEN_LINKS if s == info_path("shipping"))
    bounty = open_bounty(client, site)

    def link_claim(hunter, source_path):
        urls = [site + source_path, site + target]
        details = {"source": urls[0], "target": urls[1]}
        return claim_payload(hunter, "broken_link", urls, details)

    _, view = run_claim(client, bounty, link_claim("hunter-a.testnet", "/shop/old-shipping"))
    assert view["status"] == "paid", view
    _, duplicate = run_claim(client, bounty, link_claim("hunter-b.testnet", source))
    assert duplicate["status"] == "duplicate", duplicate
    assert len(paid_rewards(bounty["id"])) == 1


def test_links_that_redirect_to_one_missing_page_are_one_fault(client, proof_site):
    page = (
        '<html><body><a href="/a">a</a> <a href="/b">b</a> <a href="/gone">gone</a></body></html>'
    )
    token_path = "/.well-known/arsune-bounty.txt"
    ProofServer.responses = {
        "/src": (200, {"Content-Type": "text/html"}, page.encode()),
        "/a": (302, {"Location": "/gone"}, b""),
        "/b": (301, {"Location": "/gone"}, b""),
        "/gone": (404, {"Content-Type": "text/html"}, b"<html><body>Gone</body></html>"),
    }
    bounty = client.post(
        "/api/bounties",
        json={
            "url": proof_site + "/src",
            "owner_account": "owner.testnet",
            "reward_amount": REWARD,
            "bond_amount": BOND,
            "budget": FUND,
        },
    ).json()
    ProofServer.responses[token_path] = (200, {}, bounty["proof_token"].encode())
    assert client.post(f"/api/bounties/{bounty['id']}/verify").status_code == 200
    assert fund(client, bounty).status_code == 200

    def link_claim(hunter, target):
        urls = [proof_site + "/src", proof_site + target]
        return claim_payload(hunter, "broken_link", urls, {"source": urls[0], "target": urls[1]})

    _, first = run_claim(client, bounty, link_claim("hunter-a.testnet", "/a"))
    assert first["status"] == "paid", first
    for hunter, target in (("hunter-b.testnet", "/b"), ("hunter-c.testnet", "/gone")):
        _, view = run_claim(client, bounty, link_claim(hunter, target))
        assert view["status"] == "duplicate", (target, view)
    assert len(paid_rewards(bounty["id"])) == 1


def test_proven_claim_after_the_budget_is_spent_is_refunded_as_unfunded(client, site):
    bounty = open_bounty(client, site, amount=REWARD + FEE)
    paid = submit(client, bounty, broken_link(site, "hunter-a.testnet"))
    late = submit(client, bounty, overflow(site, "hunter-b.testnet"))
    seed_result(paid["job_id"])
    scan.run(paid["job_id"])
    assert claim_view(client, bounty, paid)["status"] == "paid"
    assert client.get(f"/api/bounties/{bounty['id']}").json()["status"] == "closed"

    seed_result(late["job_id"])
    scan.run(late["job_id"])
    view = claim_view(client, bounty, late)
    assert view["status"] == "unfunded" and view["outcome"] == "proven", view
    assert view["settled_at"]
    assert [row["kind"] for row in view["ledger"]] == ["bond_in", "bond_refund"]
    [refund] = [row for row in view["ledger"] if row["kind"] == "bond_refund"]
    assert refund["amount"] == BOND and refund["tx_id"].startswith("dry-")
    assert refund["memo"] == f"arsune:refund:{late['id']}"
    detail = client.get(f"/api/bounties/{bounty['id']}").json()
    assert detail["status"] == "closed" and detail["budget_amount"] == 0
    assert len(paid_rewards(bounty["id"])) == 1
    assert {h["account"]: h for h in detail["hunters"]}["hunter-b.testnet"]["unfunded"] == 1
    with Session() as session:
        assert session.get(Claim, late["id"]).final_fingerprint is None

    scan.run(late["job_id"])  # a repeated message neither pays nor refunds again
    assert len([r for r in ledger(bounty["id"], late["id"]) if r.kind == "bond_refund"]) == 1
    rejected = client.post(
        f"/api/bounties/{bounty['id']}/claims", json=broken_link(site, "hunter-c.testnet")
    )
    assert rejected.status_code == 409 and "closed" in rejected.json()["detail"]
