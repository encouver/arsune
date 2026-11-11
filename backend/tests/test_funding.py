"""Owner funding: 1Click deposit addresses against a fake 1Click server, simulated funds,
the demo-site proof helper, and migration 0007."""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.bounty import crosschain, settlement
from app.browser.policy import origin
from app.core.config import settings
from app.core.db import Session, engine
from app.core.models import (
    Attempt,
    Base,
    Bounty,
    Event,
    FundingRequest,
    Job,
    LedgerEntry,
    PendingAction,
    Record,
    utcnow,
)
from app.main import app
from app.workers import coordinator
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import delete, inspect, select, text
from sqlalchemy.exc import IntegrityError

ARSUNE = "arsune-test.near"
USDC_BASE = "nep141:base-0x833589fcd6edb6e08f4c7c32d4f71b54bda02913.omft.near"
ETH = "nep141:eth.omft.near"
TOKENS = [
    {
        "assetId": USDC_BASE,
        "decimals": 6,
        "blockchain": "base",
        "symbol": "USDC",
        "price": 1,
        "priceUpdatedAt": "2026-11-09T10:00:00Z",
        "contractAddress": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
    },
    {
        "assetId": ETH,
        "decimals": 18,
        "blockchain": "eth",
        "symbol": "ETH",
        "price": 3000,
        "priceUpdatedAt": "2026-11-09T10:00:00Z",
        "contractAddress": None,
    },
    {"assetId": "broken"},
]


class FakeOneClick:
    """Answers /v0/tokens, /v0/quote and /v0/status the way the 1Click OpenAPI describes."""

    def __init__(self):
        self.calls = []
        self.statuses = {}
        self.down = False
        self.count = 0
        # 1Click can keep an address open longer than the refund deadline that was asked.
        self.address_lifetime = timedelta(0)
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parts = urlsplit(self.path)
                query = {k: v[0] for k, v in parse_qs(parts.query).items()}
                fake.calls.append(("GET", parts.path, query, dict(self.headers)))
                if fake.down:
                    return self.reply(503, {"message": "maintenance"})
                if parts.path == "/v0/tokens":
                    return self.reply(200, TOKENS)
                if parts.path == "/v0/status":
                    status = fake.statuses.get(query.get("depositAddress"))
                    if status is None:
                        return self.reply(404, {"message": "Deposit address not found"})
                    return self.reply(200, status)
                return self.reply(404, {"message": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                fake.calls.append(("POST", self.path, body, dict(self.headers)))
                if fake.down:
                    return self.reply(503, {"message": "maintenance"})
                if body["refundTo"] == "0xnot-an-address":
                    return self.reply(400, {"message": "refundTo is not a valid base address"})
                fake.count += 1
                address = f"0x{fake.count:040x}"
                closes = crosschain.parse_time(body["deadline"]) + fake.address_lifetime
                return self.reply(
                    200,
                    {
                        "correlationId": str(uuid4()),
                        "timestamp": "2026-11-09T10:00:00Z",
                        "signature": "ed25519:fake-signature",
                        "quoteRequest": body,
                        "quote": {
                            "depositAddress": address,
                            "amountIn": "5050000",
                            "amountInFormatted": "5.05",
                            "amountInUsd": "5.05",
                            "minAmountIn": "5001000",
                            "amountOut": body["amount"],
                            "amountOutFormatted": "5",
                            "amountOutUsd": "5",
                            "minAmountOut": body["amount"],
                            "deadline": crosschain.iso(closes),
                            "timeWhenInactive": crosschain.iso(closes),
                            "timeEstimate": 40,
                        },
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def status(self, address, status, **details):
        quote = next(c[2] for c in self.calls if c[0] == "POST")
        self.statuses[address] = {
            "correlationId": str(uuid4()),
            "quoteResponse": {"quoteRequest": quote},
            "status": status,
            "updatedAt": "2026-11-09T10:01:00Z",
            "swapDetails": {
                "intentHashes": [],
                "nearTxHashes": [],
                "originChainTxHashes": [],
                "destinationChainTxHashes": [],
                **details,
            },
        }

    def status_calls(self, address=None):
        return [
            c
            for c in self.calls
            if c[1] == "/v0/status" and address in (None, c[2].get("depositAddress"))
        ]


@pytest.fixture
def oneclick(monkeypatch):
    fake = FakeOneClick()
    monkeypatch.setattr(crosschain.oneclick, "url", fake.url)
    monkeypatch.setattr(crosschain.oneclick, "jwt", SecretStr("partner-jwt"))
    monkeypatch.setattr(crosschain, "_tokens", {"items": None, "at": 0.0})
    monkeypatch.setattr(crosschain, "_backoff", {"until": 0.0})
    monkeypatch.setattr(crosschain, "_poll", {"at": 0.0})
    yield fake
    fake.server.shutdown()
    fake.server.server_close()


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "settlement_mode", "dry")
    monkeypatch.setattr(settings, "near_account_id", "")
    monkeypatch.setattr(settings, "near_private_key", SecretStr(""))
    monkeypatch.setattr(settings, "near_rpc_url", "http://127.0.0.1:9/")
    monkeypatch.setattr(settlement, "_salt", {"value": None, "at": 0.0})
    created = []
    test_client = TestClient(app)
    test_client.created = created
    yield test_client
    with Session.begin() as session:
        urls = select(Bounty.url).where(Bounty.id.in_(created))
        checks = session.scalars(
            select(Job.id).where(Job.kind == "site_check", Job.url.in_(urls))
        ).all()
        session.execute(delete(FundingRequest).where(FundingRequest.bounty_id.in_(created)))
        session.execute(delete(LedgerEntry).where(LedgerEntry.bounty_id.in_(created)))
        session.execute(delete(Bounty).where(Bounty.id.in_(created)))
        for model in (Record, Event, Attempt, PendingAction):
            session.execute(delete(model).where(model.job_id.in_(checks)))
        session.execute(delete(Job).where(Job.id.in_(checks)))


@pytest.fixture
def live(client, monkeypatch, oneclick):
    """near_intents mode for the 1Click path only; no test here reaches the NEAR RPC."""
    monkeypatch.setattr(settings, "settlement_mode", "near_intents")
    monkeypatch.setattr(settings, "near_account_id", ARSUNE)
    return client


def add_bounty(client, status="open", url="https://shop.example/", budget=0, closed_reason=None):
    bounty_id = str(uuid4())
    with Session.begin() as session:
        session.add(
            Bounty(
                id=bounty_id,
                url=url,
                origin=origin(url),
                owner_account="owner.near",
                proof_token="token-" + uuid4().hex,
                status=status,
                closed_reason=closed_reason,
                reward_amount=1_000_000,
                bond_amount=100_000,
                fee_bps=1000,
                budget_amount=budget,
                budget_target=10_000_000,
            )
        )
    client.created.append(bounty_id)
    return bounty_id


def request_address(client, bounty_id, amount=5_000_000, asset=USDC_BASE, refund="0xabc"):
    return client.post(
        f"/api/bounties/{bounty_id}/funding-address",
        json={"amount": amount, "origin_asset": asset, "refund_to": refund},
    )


def budget(bounty_id):
    with Session() as session:
        return session.get(Bounty, bounty_id).budget_amount


def fund_rows(bounty_id):
    with Session() as session:
        return session.scalars(
            select(LedgerEntry).where(
                LedgerEntry.bounty_id == bounty_id, LedgerEntry.kind == "fund"
            )
        ).all()


def test_dry_mode_offers_simulation_and_refuses_crosschain(client, oneclick):
    options = client.get("/api/funding/options").json()
    assert options["settlement_mode"] == "dry"
    assert options["simulated_funding"] is True
    assert options["arsune_account"] == "arsune.dry"
    assert options["crosschain"] == {
        "available": False,
        "reason": crosschain.DRY_REASON,
        "tokens": [],
    }
    bounty_id = add_bounty(client)
    response = request_address(client, bounty_id)
    assert response.status_code == 409
    assert "dry mode" in response.json()["detail"]
    # The judge stack is offline: dry mode never calls 1Click.
    assert oneclick.calls == []


def test_live_options_list_tokens_or_explain_the_outage(live, oneclick):
    options = live.get("/api/funding/options").json()
    assert options["crosschain"]["available"] is True
    assert options["simulated_funding"] is False
    assets = [t["asset_id"] for t in options["crosschain"]["tokens"]]
    assert assets == [USDC_BASE, ETH]
    live.get("/api/funding/options")
    assert len([c for c in oneclick.calls if c[1] == "/v0/tokens"]) == 1, "tokens are cached"

    crosschain._tokens.update(items=None, at=0.0)
    oneclick.down = True
    options = live.get("/api/funding/options").json()
    assert options["crosschain"] == {
        "available": False,
        "reason": crosschain.OFFLINE_REASON,
        "tokens": [],
    }


def test_quote_asks_for_exact_usdc_into_arsunes_intents_account(live, oneclick):
    bounty_id = add_bounty(live)
    before = utcnow()
    response = request_address(live, bounty_id)
    assert response.status_code == 201, response.text
    view = response.json()
    _, path, body, headers = next(c for c in oneclick.calls if c[0] == "POST")
    assert path == "/v0/quote"
    assert headers["authorization"] == "Bearer partner-jwt"
    deadline = crosschain.parse_time(body.pop("deadline"))
    assert timedelta(minutes=59) < deadline - before < timedelta(minutes=61)
    assert body == {
        "dry": False,
        "swapType": "EXACT_OUTPUT",
        "slippageTolerance": 100,
        "originAsset": USDC_BASE,
        "depositType": "ORIGIN_CHAIN",
        "destinationAsset": settings.bounty_token,
        "amount": "5000000",
        "refundTo": "0xabc",
        "refundType": "ORIGIN_CHAIN",
        "recipient": ARSUNE,
        "recipientType": "INTENTS",
    }
    assert view["deposit_address"] == f"0x{1:040x}"
    assert view["status"] == "pending_deposit"
    assert view["amount"] == 5_000_000
    assert (view["amount_in"], view["amount_in_formatted"]) == ("5050000", "5.05")
    assert view["min_amount_in_formatted"] == "5.001"
    assert (view["origin_symbol"], view["origin_chain"]) == ("USDC", "base")
    assert view["time_estimate"] == 40
    with Session() as session:
        row = session.get(FundingRequest, view["id"])
        assert row.quote["signature"] == "ed25519:fake-signature"
        assert row.settlement_mode == "near_intents"
    listed = live.get(f"/api/bounties/{bounty_id}/funding").json()
    assert [r["id"] for r in listed["requests"]] == [view["id"]]
    assert listed["requests"][0]["status"] == "pending_deposit"


def test_quote_errors_are_clear(live, oneclick):
    bounty_id = add_bounty(live)
    response = request_address(live, bounty_id, asset="nep141:unknown.near")
    assert response.status_code == 422
    assert response.json()["detail"] == "1Click does not support this origin asset."
    response = request_address(live, bounty_id, refund="0xnot-an-address")
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "1Click refused the quote: refundTo is not a valid base address"
    )
    response = request_address(live, add_bounty(live, status="pending_proof"))
    assert response.status_code == 409
    assert "Prove ownership" in response.json()["detail"]
    response = request_address(live, bounty_id, amount=0)
    assert response.status_code == 422
    assert request_address(live, str(uuid4())).status_code == 404
    for _ in range(5):
        assert request_address(live, bounty_id).status_code == 201
    response = request_address(live, bounty_id)
    assert response.status_code == 429
    oneclick.down = True
    crosschain._tokens.update(items=None, at=0.0)
    response = request_address(live, add_bounty(live))
    assert response.status_code == 503
    assert response.json()["detail"] == (
        "No deposit address was made. The 1Click API answered HTTP 503."
    )


def test_status_transitions_then_credit_exactly_once(live, oneclick, monkeypatch):
    monkeypatch.setattr(crosschain, "STATUS_SECONDS", 0)
    bounty_id = add_bounty(live, budget=1_000_000)
    view = request_address(live, bounty_id).json()
    address = view["deposit_address"]

    def listed():
        response = live.get(f"/api/bounties/{bounty_id}/funding")
        assert response.status_code == 200, response.text
        return response.json()["requests"][0]

    assert listed()["status"] == "pending_deposit", "an unknown address keeps its status"
    for raw, local in [
        ("PENDING_DEPOSIT", "pending_deposit"),
        ("KNOWN_DEPOSIT_TX", "deposit_seen"),
        ("PROCESSING", "processing"),
        ("INCOMPLETE_DEPOSIT", "incomplete_deposit"),
        ("PROCESSING", "processing"),
    ]:
        oneclick.status(address, raw)
        row = listed()
        assert (row["status"], row["oneclick_status"], row["final"]) == (local, raw, False)
        assert budget(bounty_id) == 1_000_000
    oneclick.status(
        address, "SUCCESS", amountOut="5000000", nearTxHashes=["FirstHash", "SettleHash"]
    )
    row = listed()
    assert (row["status"], row["final"], row["delivered_amount"]) == ("credited", True, 5_000_000)
    assert row["note"] is None
    assert budget(bounty_id) == 6_000_000
    (entry,) = fund_rows(bounty_id)
    assert entry.memo == f"arsune:fund:1click:{address}"
    assert (entry.amount, entry.tx_id, entry.status) == (5_000_000, "SettleHash", "completed")
    assert (entry.account, entry.settlement_mode) == ("owner.near", "near_intents")
    assert row["ledger_entry_id"] == entry.id

    calls = len(oneclick.status_calls(address))
    listed()
    crosschain.poll_funding()
    assert len(oneclick.status_calls(address)) == calls, "a credited request is not polled again"

    # A second process that saw SUCCESS before the first one committed credits nothing more.
    with Session.begin() as session:
        session.get(FundingRequest, view["id"]).status = "processing"
    crosschain.apply_status(view["id"], oneclick.statuses[address], utcnow())
    assert budget(bounty_id) == 6_000_000
    assert len(fund_rows(bounty_id)) == 1
    detail = live.get(f"/api/bounties/{bounty_id}").json()
    assert [r["kind"] for r in detail["ledger"]] == ["fund"]
    assert detail["ledger"][0]["explorer_url"] == "https://nearblocks.io/txns/SettleHash"


def test_parallel_success_reports_credit_once(live, oneclick):
    bounty_id = add_bounty(live)
    view = request_address(live, bounty_id).json()
    oneclick.status(view["deposit_address"], "SUCCESS", amountOut="5000000")
    status = oneclick.statuses[view["deposit_address"]]
    threads = [
        threading.Thread(target=crosschain.apply_status, args=(view["id"], status, utcnow()))
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert budget(bounty_id) == 5_000_000
    assert len(fund_rows(bounty_id)) == 1


def test_credit_uses_the_amount_1click_delivered(live, oneclick):
    bounty_id = add_bounty(live)
    short = request_address(live, bounty_id).json()
    oneclick.status(short["deposit_address"], "SUCCESS", amountOut="4990000")
    assert crosschain.refresh(short["id"], force=True)
    with Session() as session:
        row = session.get(FundingRequest, short["id"])
        assert (row.status, row.delivered_amount) == ("credited", 4_990_000)
        assert "less than the 5000000 asked" in row.note
    assert budget(bounty_id) == 4_990_000

    missing = request_address(live, bounty_id).json()
    oneclick.status(missing["deposit_address"], "SUCCESS")
    assert crosschain.refresh(missing["id"], force=True)
    other = request_address(live, bounty_id).json()
    oneclick.status(other["deposit_address"], "SUCCESS", amountOut="5000000")
    oneclick.statuses[other["deposit_address"]]["quoteResponse"]["quoteRequest"] = {
        "recipient": "someone-else.near"
    }
    assert crosschain.refresh(other["id"], force=True)
    with Session() as session:
        for request_id in (missing["id"], other["id"]):
            row = session.get(FundingRequest, request_id)
            assert row.status == "needs_review"
            assert row.ledger_entry_id is None
            assert "Nothing was credited" in row.note
    assert budget(bounty_id) == 4_990_000
    assert len(fund_rows(bounty_id)) == 1


def bounty_state(bounty_id):
    with Session() as session:
        bounty = session.get(Bounty, bounty_id)
        return bounty.status, bounty.closed_reason, bounty.budget_amount


def test_a_1click_deposit_reopens_a_bounty_whose_budget_ran_out(live, oneclick):
    spent = add_bounty(live, status="closed", closed_reason="budget_spent", budget=500_000)
    view = request_address(live, spent).json()
    oneclick.status(view["deposit_address"], "SUCCESS", amountOut="5000000")
    assert crosschain.refresh(view["id"], force=True)
    assert bounty_state(spent) == ("open", None, 5_500_000)
    with Session() as session:
        row = session.get(FundingRequest, view["id"])
        assert row.status == "credited"
        assert "closed because its budget ran out. These funds opened it." in row.note

    # The deposit arrives after the bounty closed for another reason: credited, still closed.
    other = add_bounty(live, status="closed", closed_reason="budget_spent")
    late = request_address(live, other).json()
    with Session.begin() as session:
        session.get(Bounty, other).closed_reason = "owner_closed"
    oneclick.status(late["deposit_address"], "SUCCESS", amountOut="5000000")
    assert crosschain.refresh(late["id"], force=True)
    assert bounty_state(other) == ("closed", "owner_closed", 5_000_000)
    with Session() as session:
        assert session.get(FundingRequest, late["id"]).note == (
            "The bounty was closed when the funds arrived."
        )
    response = request_address(live, other)
    assert response.status_code == 409
    assert "budget ran out" in response.json()["detail"]


def test_refund_and_failure_are_final_without_credit(live, oneclick):
    bounty_id = add_bounty(live)
    for raw, local in [("REFUNDED", "refunded"), ("FAILED", "failed")]:
        view = request_address(live, bounty_id).json()
        oneclick.status(view["deposit_address"], raw, refundedAmount="5050000")
        assert crosschain.refresh(view["id"], force=True)
        with Session() as session:
            assert session.get(FundingRequest, view["id"]).status == local
    assert budget(bounty_id) == 0
    assert fund_rows(bounty_id) == []


def test_deadline_expiry(live, oneclick):
    bounty_id = add_bounty(live)
    idle = request_address(live, bounty_id).json()
    busy = request_address(live, bounty_id).json()
    oneclick.status(idle["deposit_address"], "PENDING_DEPOSIT")
    oneclick.status(busy["deposit_address"], "PROCESSING")
    deadline = crosschain.parse_time(idle["deadline"])

    assert crosschain.refresh(idle["id"], now=deadline + timedelta(minutes=5))
    with Session() as session:
        assert session.get(FundingRequest, idle["id"]).status == "pending_deposit", "grace"
    after = deadline + crosschain.EXPIRY_GRACE + timedelta(seconds=1)
    for request in (idle, busy):
        assert crosschain.refresh(request["id"], now=after)
    with Session() as session:
        expired = session.get(FundingRequest, idle["id"])
        assert expired.status == "expired"
        assert "address closed without a deposit" in expired.note
        assert session.get(FundingRequest, busy["id"]).status == "processing"
    calls = len(oneclick.status_calls())
    assert crosschain.refresh(idle["id"], now=after + timedelta(hours=1), force=True)
    assert len(oneclick.status_calls()) == calls


def test_address_that_1click_does_not_know_expires_after_it_closes(live, oneclick):
    bounty_id = add_bounty(live)
    view = request_address(live, bounty_id).json()
    closes = crosschain.parse_time(view["address_closes_at"])
    # The fake has no status for the address, so /v0/status answers 404.
    assert crosschain.refresh(view["id"], now=closes + timedelta(minutes=1), force=True)
    with Session() as session:
        assert session.get(FundingRequest, view["id"]).status == "pending_deposit"
    assert crosschain.refresh(view["id"], now=closes + timedelta(hours=24), force=True)
    with Session() as session:
        row = session.get(FundingRequest, view["id"])
        assert row.status == "expired"
        assert row.oneclick_status is None


def test_open_address_is_watched_after_send_before_until_it_closes(live, oneclick):
    oneclick.address_lifetime = timedelta(days=3)
    bounty_id = add_bounty(live)
    view = request_address(live, bounty_id).json()
    send_before = crosschain.parse_time(view["deadline"])
    closes = crosschain.parse_time(view["address_closes_at"])
    assert closes - send_before == timedelta(days=3)
    oneclick.status(view["deposit_address"], "PENDING_DEPOSIT")
    late = send_before + crosschain.EXPIRY_GRACE + timedelta(minutes=1)
    assert crosschain.refresh(view["id"], now=late)
    calls = len(oneclick.status_calls())
    assert crosschain.refresh(view["id"], now=late + timedelta(minutes=1))
    assert len(oneclick.status_calls()) == calls, "a late address is checked every 5 minutes"
    oneclick.status(view["deposit_address"], "SUCCESS", amountOut="5000000")
    assert crosschain.refresh(view["id"], now=late + timedelta(minutes=6))
    assert budget(bounty_id) == 5_000_000, "a late deposit is still credited"

    other = request_address(live, bounty_id).json()
    oneclick.status(other["deposit_address"], "PENDING_DEPOSIT")
    assert crosschain.refresh(other["id"], now=closes + crosschain.EXPIRY_GRACE)
    with Session() as session:
        assert session.get(FundingRequest, other["id"]).status == "pending_deposit"
    assert crosschain.refresh(other["id"], now=closes + timedelta(hours=1))
    with Session() as session:
        assert session.get(FundingRequest, other["id"]).status == "expired"


def test_unreachable_1click_keeps_last_status(live, oneclick, monkeypatch):
    monkeypatch.setattr(crosschain, "STATUS_SECONDS", 0)
    bounty_id = add_bounty(live)
    view = request_address(live, bounty_id).json()
    oneclick.status(view["deposit_address"], "PROCESSING")
    assert live.get(f"/api/bounties/{bounty_id}/funding").json()["status_error"] is None
    oneclick.down = True
    listed = live.get(f"/api/bounties/{bounty_id}/funding").json()
    assert listed["status_error"].startswith("1Click cannot be reached")
    assert listed["requests"][0]["status"] == "processing"
    calls = len(oneclick.calls)
    live.get(f"/api/bounties/{bounty_id}/funding")
    assert len(oneclick.calls) == calls, "reads back off after a failure"


def test_coordinator_credits_without_a_reader(live, oneclick):
    bounty_id = add_bounty(live)
    view = request_address(live, bounty_id).json()
    oneclick.status(view["deposit_address"], "SUCCESS", amountOut="5000000")
    coordinator.tick()
    assert budget(bounty_id) == 5_000_000
    crosschain._poll["at"] = 0.0
    coordinator.tick()
    assert budget(bounty_id) == 5_000_000
    assert len(fund_rows(bounty_id)) == 1


def test_simulated_funding_uses_the_signed_transfer_path(client):
    bounty_id = add_bounty(client)
    response = client.post(f"/api/bounties/{bounty_id}/fund-simulated", json={"amount": 7_000_000})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["simulated"] is True
    assert body["memo"] == f"arsune:fund:{body['fund_id']}"
    assert len(body["sender"]) == 64
    assert body["bounty"]["budget_amount"] == 7_000_000
    (entry,) = fund_rows(bounty_id)
    assert (entry.id, entry.memo, entry.account) == (body["fund_id"], body["memo"], body["sender"])
    assert entry.tx_id.startswith("dry-")
    assert entry.settlement_mode == "dry"

    closed = add_bounty(client, status="pending_proof")
    response = client.post(f"/api/bounties/{closed}/fund-simulated", json={"amount": 1})
    assert response.status_code == 409
    settings.settlement_mode = "near_intents"
    response = client.post(f"/api/bounties/{bounty_id}/fund-simulated", json={"amount": 1})
    assert response.status_code == 409
    assert "dry settlement mode" in response.json()["detail"]
    assert budget(bounty_id) == 7_000_000


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def demo_site():
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
                    if httpx.get(url + "/shop/", timeout=0.2).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("Demo did not start.")
            yield url
        finally:
            process.terminate()
            process.wait(timeout=5)


def test_demo_site_proof_then_verify(client, demo_site, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "demo_url", demo_site)
    monkeypatch.setattr(settings, "evidence_dir", tmp_path)
    options = client.get("/api/funding/options").json()
    assert options["demo_site_url"] == demo_site
    assert options["demo_site_origin"] == origin(demo_site)
    response = client.post(
        "/api/bounties",
        json={
            "url": demo_site + "/shop/",
            "owner_account": "owner.near",
            "reward_amount": 1_000_000,
            "bond_amount": 100_000,
            "budget": 10_000_000,
        },
    )
    assert response.status_code == 201, response.text
    bounty = response.json()
    client.created.append(bounty["id"])
    response = client.post(f"/api/bounties/{bounty['id']}/demo-proof")
    assert response.status_code == 200, response.text
    assert response.json()["proof_url"] == demo_site + "/.well-known/arsune-bounty.txt"
    assert httpx.get(response.json()["proof_url"]).text.strip() == bounty["proof_token"]
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 202 and response.json()["status"] == "pending_proof"
    # The worker's real browser loads the demo shop: Arsune can check it, so the bounty opens.
    with Session() as session:
        scan.run(session.get(Bounty, bounty["id"]).site_check_job_id)
    view = client.get(f"/api/bounties/{bounty['id']}").json()
    assert (view["status"], view["site_check"]["state"]) == ("open", "passed"), view["site_check"]
    assert client.post(f"/api/bounties/{bounty['id']}/demo-proof").status_code == 409

    other = add_bounty(client, status="pending_proof")
    response = client.post(f"/api/bounties/{other}/demo-proof")
    assert response.status_code == 409
    assert "local demo site" in response.json()["detail"]


def test_migration_0007_adds_and_removes_funding_requests():
    schema = f"arsune_funding_test_{uuid4().hex}"
    config = Config("alembic.ini")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "0006")
            assert "funding_requests" not in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "0007")
            assert "funding_requests" in inspect(connection).get_table_names(schema=schema)
            # Only the columns of later revisions (0008 to 0010) differ from the models, with
            # the index and foreign key of 0010's site check column.
            later = compare_metadata(MigrationContext.configure(connection), Base.metadata)
            assert {(d[2], d[3].name) for d in later if d[0] == "add_column"} == {
                ("bounties", "closed_reason"),
                ("bounties", "site_check_job_id"),
                ("bounties", "paused_reason"),
                ("bounties", "unchecked_claims"),
                ("claims", "evidence_hash"),
                ("claims", "evidence_bundle"),
            }
            assert {d[0] for d in later} <= {"add_column", "add_index", "add_fk"}
            connection.execute(
                text("""INSERT INTO bounties (id, url, origin, owner_account, proof_token, status,
                    reward_amount, bond_amount, fee_bps, budget_amount, budget_target,
                    created_at, updated_at) VALUES ('b1', 'https://site.test/',
                    'https://site.test:443', 'owner.near', 'token', 'open', 1000000, 100000,
                    1000, 0, 5000000, now(), now())""")
            )
            insert = text("""INSERT INTO funding_requests (id, bounty_id, status, amount,
                origin_asset, origin_symbol, origin_chain, origin_decimals, deposit_address,
                deposit_memo, amount_in, amount_in_formatted, min_amount_in, refund_to,
                deadline, quote, settlement_mode, created_at, updated_at) VALUES (:id, 'b1',
                'pending_deposit', 5000000, 'a', 'USDC', 'base', 6, '0xdep', :memo, '1', '1',
                '1', '0xabc', now(), '{}', 'near_intents', now(), now())""")
            connection.execute(insert, {"id": "f1", "memo": ""})
            connection.execute(insert, {"id": "f2", "memo": "42"})
            with (
                pytest.raises(IntegrityError, match="uq_funding_requests_deposit"),
                connection.begin_nested(),
            ):
                connection.execute(insert, {"id": "f3", "memo": ""})
            command.downgrade(config, "0006")
            assert "funding_requests" not in inspect(connection).get_table_names(schema=schema)
            assert "bounties" in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
