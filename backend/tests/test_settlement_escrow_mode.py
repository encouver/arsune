"""ARSUNE_SETTLEMENT_MODE=escrow: the bounty flow on the escrow contract, with an in-memory chain.

FakeChain answers the intents.near and escrow calls that escrow mode sends, and applies the
contract's money rules (docs/ESCROW.md), so these tests check what Arsune sends and records.
The contract itself is tested in contracts/escrow (unit and sandbox tests).
"""

import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from uuid import uuid4

import httpx
import pytest
from app import verify
from app.bounty import reputation, settlement
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Job, utcnow
from app.main import app
from app.settlement import escrow, near_intents
from app.settlement.base import SettlementError
from app.settlement.near_tx import OutcomeUnknown, generate_key
from app.workers import coordinator, store
from app.workers.tasks import scan
from fastapi.testclient import TestClient
from pydantic import SecretStr

# client and site are pytest fixtures; importing them shares the demo site setup, and the
# tests take them as arguments by the same names.
# ruff: noqa: F811
from test_bounties import (  # noqa: F401
    BOND,
    CONFIRMED,
    FEE,
    KEY,
    REWARD,
    claim_view,
    client,
    expire_lease,
    seed_result,
    site,
)

ARSUNE = "arsune-test.testnet"
ESCROW = "escrow.arsune-test.testnet"
FUND = 3 * (REWARD + FEE)
REJECTED = {"status": "rejected", "checker_reason": "Fresh target returned HTTP 200."}


def event(name, **data):
    body = {"standard": "arsune-escrow", "version": "1.0.0", "event": name, "data": data}
    return "EVENT_JSON:" + json.dumps(body)


def executed(signed_list):
    return [
        {
            "account_id": json.loads(s["payload"]["message"])["signer_id"],
            "nonce": s["payload"]["nonce"],
        }
        for s in signed_list
    ]


class FakeChain:
    """intents.near and the escrow in memory. It never opens a network connection."""

    bounties: ClassVar[dict] = {}
    claims: ClassVar[dict] = {}
    sent: ClassVar[list] = []
    unknown: ClassVar[set] = set()
    rule_as: ClassVar[str | None] = None  # the escrow's outcome, to test a disagreement

    def __init__(self, url=None, timeout=30.0):
        pass

    @classmethod
    def reset(cls):
        cls.bounties, cls.claims, cls.sent, cls.unknown, cls.rule_as = {}, {}, [], set(), None

    def view(self, contract, method, args):
        if method == "current_salt":
            return "252812b3"
        if method == "simulate_intents":
            return {"intents_executed": executed(args["signed"])}
        assert contract == ESCROW, (contract, method)
        if method == "get_config":
            return {"token_id": settings.bounty_token}
        if method == "get_bounty":
            return self.bounties.get(args["bounty_id"])
        if method == "get_claim":
            return self.claims.get(args["claim_id"])
        raise AssertionError(method)

    def call_function(self, signer_id, key, receiver_id, method, args, *, gas, deposit=0):
        FakeChain.sent.append(method)
        if method in self.unknown:
            raise OutcomeUnknown("no final outcome from NEAR RPC", "UnknownHash")
        logs = getattr(self, method)(signer_id, args)
        outcome = {
            "status": {"SuccessValue": ""},
            "receipts_outcome": [{"outcome": {"logs": logs}}],
        }
        return f"EscrowTx{len(FakeChain.sent)}", outcome

    def create_bounty(self, signer_id, args):
        if args["bounty_id"] in self.bounties:
            raise SettlementError("bounty_id is already used")
        assert signer_id in (args["owner"], args["judge"])
        self.bounties[args["bounty_id"]] = {**args, "balance": 0, "status": "open"}
        return [event("create", **args)]

    def execute_intents(self, signer_id, args):
        [signed] = args["signed"]
        message = json.loads(signed["payload"]["message"])
        [intent] = message["intents"]
        assert intent["receiver_id"] == ESCROW
        amount = int(intent["tokens"][settings.bounty_token])
        sender, msg = message["signer_id"], json.loads(intent["msg"])
        logs = [
            "EVENT_JSON:"
            + json.dumps(
                {"standard": "dip4", "event": "intents_executed", "data": executed([signed])}
            )
        ]
        if "fund" in msg:
            bounty = self.bounties[msg["fund"]]
            bounty["balance"] += amount
            return [*logs, event("fund", bounty_id=msg["fund"], amount=str(amount))]
        bond = msg["bond"]
        bounty = self.bounties[bond["bounty_id"]]
        if bounty["balance"] < self.cost(bounty):
            return [*logs, event("refund", sender_id=sender, reason="budget_too_low")]
        self.claims[bond["claim_id"]] = {
            "claim_id": bond["claim_id"],
            "bounty_id": bond["bounty_id"],
            "hunter": sender,
            "bond": str(amount),
            "scout": bond.get("scout"),
            "outcome": None,
            "evidence_hash": None,
        }
        return [*logs, event("bond", **bond, hunter=sender, amount=str(amount))]

    @staticmethod
    def cost(bounty):
        reward = int(bounty["reward"])
        return reward + reward * bounty["fee_bps"] // 10_000

    def rule(self, signer_id, args):
        claim = self.claims[args["claim_id"]]
        bounty = self.bounties[claim["bounty_id"]]
        if signer_id != bounty["judge"] or claim["outcome"]:
            raise SettlementError("only the judge rules, once")
        funded = bounty["balance"] >= self.cost(bounty)
        if args["outcome"] == "unfunded" and funded:
            raise SettlementError("the bounty can pay this claim")
        outcome = self.rule_as or args["outcome"]
        if outcome == "proven" and not funded:
            outcome = "unfunded"
        bond = int(claim["bond"])
        if outcome == "proven":
            bounty["balance"] -= self.cost(bounty)
        elif outcome == "rejected":
            bounty["balance"] += bond
        claim["outcome"], claim["evidence_hash"] = outcome, args["evidence_hash"]
        data = {**args, "ruling": args["outcome"], "outcome": outcome}
        return [event("rule", bounty_id=claim["bounty_id"], **data)]

    def withdraw_remaining(self, signer_id, args):
        bounty = self.bounties[args["bounty_id"]]
        amount, bounty["balance"], bounty["status"] = bounty["balance"], 0, "closed"
        return [event("withdraw", bounty_id=args["bounty_id"], amount=str(amount))]


@pytest.fixture
def chain(client, monkeypatch):
    arsune_key, _ = generate_key()
    FakeChain.reset()
    monkeypatch.setattr(escrow, "NearRpc", FakeChain)
    monkeypatch.setattr(near_intents, "NearRpc", FakeChain)
    monkeypatch.setattr(verify, "NearRpc", FakeChain)
    monkeypatch.setattr(settings, "settlement_mode", "escrow")
    monkeypatch.setattr(settings, "near_account_id", ARSUNE)
    monkeypatch.setattr(settings, "near_private_key", SecretStr(arsune_key))
    monkeypatch.setattr(settings, "escrow_contract", ESCROW)
    return FakeChain


def deposit(sender, amount, msg, memo):
    return escrow.sign_deposit(
        sender,
        KEY,
        ESCROW,
        settings.bounty_token,
        amount,
        msg,
        memo,
        datetime.now(UTC) + timedelta(minutes=5),
        salt=bytes.fromhex("252812b3"),
    )


def fund(client, bounty, amount=FUND):
    fund_id = str(uuid4())
    memo, msg = f"arsune:fund:{fund_id}", escrow.fund_msg(bounty["id"])
    return client.post(
        f"/api/bounties/{bounty['id']}/fund",
        json={
            "fund_id": fund_id,
            "sender": "owner.testnet",
            "amount": amount,
            "signed_intent": deposit("owner.testnet", amount, msg, memo),
        },
    )


def open_bounty(client, site, **terms):
    body = {
        "url": site + "/faulty/",
        "owner_account": "owner.testnet",
        "reward_amount": REWARD,
        "bond_amount": BOND,
        "budget": FUND,
    }
    bounty = client.post("/api/bounties", json=body | terms).json()
    httpx.post(site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 200, response.text
    response = fund(client, bounty)
    assert response.status_code == 200, response.text
    return response.json()


def submit_claim(client, bounty, hunter, scout=None, bond=BOND):
    """Submit an overflow claim on its own page and return the queued claim."""
    claim_id = str(uuid4())
    url = f"{bounty['origin']}/p/{claim_id}"  # a page of its own, so no claim is a duplicate
    share = (scout["account"], scout["share_bps"]) if scout else None
    msg = escrow.bond_msg(bounty["id"], claim_id, share)
    payload = {
        "claim_id": claim_id,
        "hunter_account": hunter,
        "claim_type": "horizontal_overflow",
        "urls": [url],
        "details": {"url": url},
        "explanation": "The page is wider than the phone.",
        "reproduction_steps": ["Open the page at 390 pixels."],
        "bond_intent": deposit(hunter, bond, msg, f"arsune:bond:{claim_id}"),
    }
    if scout:
        payload["scout"] = scout
    response = client.post(f"/api/bounties/{bounty['id']}/claims", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


def claim(client, bounty, hunter, result, scout=None, bond=BOND):
    """Submit a claim, then decide it with a seeded check result."""
    created = submit_claim(client, bounty, hunter, scout, bond)
    seed_result(created["job_id"], result)
    scan.run(created["job_id"])
    return claim_view(client, bounty, created)


def rows(view):
    return {row["kind"]: row for row in view["ledger"]}


def test_escrow_mode_opens_funds_bonds_and_rules_on_the_escrow(client, site, chain):
    info = client.get("/api/settlement").json()
    assert info["mode"] == "escrow" and info["escrow_contract"] == ESCROW
    assert info["arsune_account"] == ARSUNE and info["salt"] == "252812b3"
    options = client.get("/api/funding/options").json()
    assert (options["settlement_mode"], options["escrow_contract"]) == ("escrow", ESCROW)
    assert not options["crosschain"]["available"]
    assert "escrow mode" in options["crosschain"]["reason"]
    high_fee = {
        "url": site + "/faulty/",
        "owner_account": "owner.testnet",
        "reward_amount": REWARD,
        "bond_amount": BOND,
        "budget": FUND,
        "fee_bps": 2500,
    }
    assert client.post("/api/bounties", json=high_fee).status_code == 422

    bounty = open_bounty(client, site)
    on_chain = chain.bounties[bounty["id"]]
    assert (on_chain["owner"], on_chain["judge"], on_chain["fee_account"]) == (
        "owner.testnet",
        ARSUNE,
        ARSUNE,
    )
    assert (on_chain["reward"], on_chain["base_bond"], on_chain["fee_bps"]) == (
        str(REWARD),
        str(BOND),
        1000,
    )
    with Session() as session:
        created = session.get(Bounty, bounty["id"]).created_at
    assert int(on_chain["deadline_ns"]) == int((created + timedelta(days=30)).timestamp()) * 10**9
    assert on_chain["balance"] == FUND and bounty["budget_amount"] == FUND
    ledger = rows(bounty)
    assert ledger["escrow_open"]["tx_id"] == "EscrowTx1" and ledger["escrow_open"]["amount"] == 0
    assert ledger["fund"]["tx_id"] == "EscrowTx2"
    assert ledger["fund"]["explorer_url"] == "https://nearblocks.io/txns/EscrowTx2"
    # Verifying again neither creates the bounty again nor adds a row.
    assert client.post(f"/api/bounties/{bounty['id']}/verify").status_code == 200
    assert chain.sent == ["create_bounty", "execute_intents"]

    # A transfer to Arsune's own account, as in near_intents mode, is not an escrow deposit.
    fund_id = str(uuid4())
    plain = near_intents.sign_transfer(
        "owner.testnet",
        KEY,
        ARSUNE,
        settings.bounty_token,
        REWARD,
        f"arsune:fund:{fund_id}",
        datetime.now(UTC) + timedelta(minutes=5),
        salt=bytes.fromhex("252812b3"),
    )
    body = {"fund_id": fund_id, "sender": "owner.testnet", "amount": REWARD, "signed_intent": plain}
    assert client.post(f"/api/bounties/{bounty['id']}/fund", json=body).status_code == 422

    scout = {"account": "scout.testnet", "share_bps": 2500, "tip_id": "tip-1"}
    paid = claim(client, bounty, "hunter-a.testnet", CONFIRMED, scout=scout)
    assert paid["status"] == "paid", paid
    assert chain.claims[paid["id"]]["scout"] == {"account_id": "scout.testnet", "share_bps": 2500}
    record = chain.claims[paid["id"]]
    assert (record["outcome"], record["evidence_hash"]) == ("proven", paid["evidence_hash"])
    ledger = rows(paid)
    tx = ledger["reward_out"]["tx_id"]
    assert ledger["bond_in"]["tx_id"] != tx and tx.startswith("EscrowTx")
    assert {kind: ledger[kind]["tx_id"] for kind in ("bond_refund", "scout_share", "fee")} == {
        "bond_refund": tx,
        "scout_share": tx,
        "fee": tx,
    }
    assert ledger["reward_out"]["amount"] == REWARD - REWARD // 4
    assert ledger["scout_share"]["amount"] == REWARD // 4
    assert ledger["fee"]["amount"] == FEE and ledger["fee"]["account"] == ARSUNE

    rejected = claim(client, bounty, "hunter-b.testnet", REJECTED)
    assert rejected["status"] == "rejected" and rejected["settled_at"]
    assert rows(rejected)["bond_forfeit"]["tx_id"].startswith("EscrowTx")
    assert chain.claims[rejected["id"]]["outcome"] == "rejected"
    detail = client.get(f"/api/bounties/{bounty['id']}").json()
    assert detail["budget_amount"] == FUND - REWARD - FEE + BOND
    assert chain.bounties[bounty["id"]]["balance"] == detail["budget_amount"]
    assert chain.sent.count("rule") == 2
    assert {row["settlement_mode"] for row in detail["ledger"]} == {"escrow"}


def test_escrow_rule_is_sent_once_and_disagreements_go_to_review(client, site, chain):
    bounty = open_bounty(client, site)
    chain.unknown.add("rule")
    unknown = claim(client, bounty, "hunter-a.testnet", CONFIRMED)
    assert unknown["status"] == "needs_review" and "UnknownHash" in unknown["settlement_note"]
    assert rows(unknown)["reward_out"]["status"] == "unknown"
    sent = len(chain.sent)
    expire_lease(unknown["job_id"])
    with Session.begin() as session:
        session.get(Job, unknown["job_id"]).status = "running"
    scan.run(unknown["job_id"])
    assert len(chain.sent) == sent  # a repeated run never sends rule again

    chain.unknown.clear()
    chain.rule_as = "unfunded"
    other = claim(client, bounty, "hunter-b.testnet", CONFIRMED)
    assert other["status"] == "needs_review", other
    assert "The escrow ruled unfunded" in other["settlement_note"]
    assert rows(other)["reward_out"]["status"] == "failed"
    assert rows(other)["reward_out"]["tx_id"] is None


def test_a_stopped_claim_job_gets_an_unverified_ruling_on_the_escrow(client, site, chain):
    bounty = open_bounty(client, site)
    created = submit_claim(client, bounty, "hunter-a.testnet")
    assert store.claim(created["job_id"])  # a worker took the job, then stopped
    with Session.begin() as session:
        job = session.get(Job, created["job_id"])
        job.deadline = job.lease_until = utcnow() - timedelta(seconds=1)
    coordinator.tick()
    view = claim_view(client, bounty, created)
    assert (view["status"], view["outcome"], view["job_status"]) == (
        "unverified",
        "unverified",
        "failed",
    ), view
    assert view["settled_at"]
    record = chain.claims[created["id"]]
    assert (record["outcome"], record["evidence_hash"]) == ("unverified", view["evidence_hash"])
    refund = rows(view)["bond_refund"]
    assert refund["amount"] == BOND and refund["tx_id"].startswith("EscrowTx")
    bundle = json.loads(client.get(view["evidence_url"]).content)
    assert bundle["ruling"]["outcome"] == "unverified"
    assert "time or attempt limit" in bundle["check"]["reason"]
    coordinator.tick()
    assert chain.sent.count("rule") == 1


def test_escrow_bond_has_no_near_intents_cap_and_withdraw_closes_for_good(
    client,
    site,
    chain,
    monkeypatch,
):
    bounty = open_bounty(client, site)
    # The near_intents cap (one transfer of at most max_payment_amount) is not used here.
    monkeypatch.setattr(settings, "max_payment_amount", REWARD)
    assert reputation.required_bond(REWARD, BOND, 30_000) == 3 * BOND
    with pytest.raises(SettlementError, match="only through rule"):
        settlement.pay("hunter.testnet", 1, "arsune:reward:x")

    response = client.post(f"/api/bounties/{bounty['id']}/withdraw")
    assert response.status_code == 200, response.text
    closed = response.json()
    assert (closed["status"], closed["closed_reason"], closed["budget_amount"]) == (
        "closed",
        "withdrawn",
        0,
    )
    withdraw = rows(closed)["withdraw"]
    assert (withdraw["amount"], withdraw["account"]) == (FUND, "owner.testnet")
    assert fund(client, bounty).status_code == 409  # a withdrawn bounty does not reopen


def test_escrow_bounty_with_other_terms_does_not_open(client, site, chain, monkeypatch):
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
    httpx.post(site + "/bounty-proof-control", json={"token": bounty["proof_token"]})
    monkeypatch.setattr(settings, "escrow_contract", "")
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 502 and "ARSUNE_ESCROW_CONTRACT" in response.text
    monkeypatch.setattr(settings, "escrow_contract", ESCROW)
    # The owner can create the escrow bounty first, with other terms.
    chain.bounties[bounty["id"]] = {"bounty_id": bounty["id"], "reward": "1"}
    response = client.post(f"/api/bounties/{bounty['id']}/verify")
    assert response.status_code == 502 and "other terms" in response.text
    assert client.get(f"/api/bounties/{bounty['id']}").json()["status"] == "pending_proof"


def test_verify_compares_the_bundle_with_the_escrow_record(
    client, site, chain, monkeypatch, capsys
):
    monkeypatch.setattr(verify.httpx, "Client", lambda base_url, timeout: TestClient(app))
    bounty = open_bounty(client, site)
    paid = claim(client, bounty, "hunter-a.testnet", CONFIRMED)
    assert verify.main(["http://testserver", paid["id"], "--no-browser"]) == 0
    out = capsys.readouterr().out
    assert f"{ESCROW}: proven, evidence_hash {paid['evidence_hash']}" in out
    assert "Escrow hash = bundle hash:" in out and "MISMATCH" not in out
    assert "Escrow outcome = ruling:    proven = proven  MATCH" in out
    chain.claims[paid["id"]]["evidence_hash"] = "0" * 64
    assert verify.main(["http://testserver", paid["id"], "--no-browser"]) == 1


def test_swarm_agents_sign_escrow_deposits():
    from swarm.identity import Identity, escrow_bond_msg, escrow_fund_msg, sign_payment

    info = {
        "mode": "escrow",
        "escrow_contract": ESCROW,
        "token": settings.bounty_token,
        "intents_contract": settings.intents_contract,
        "salt": "252812b3",
    }
    hunter = Identity.generate("hunter")
    scout = {"account": "scout.testnet", "share_bps": 100, "tip_id": "tip-1"}
    msg = escrow_bond_msg(info, "b1", "c1", scout)
    assert json.loads(msg) == {
        "bond": {
            "bounty_id": "b1",
            "claim_id": "c1",
            "scout": {"account_id": "scout.testnet", "share_bps": 100},
        }
    }
    signed = sign_payment(hunter, info, 5, "arsune:bond:c1", msg=msg)
    escrow.verify_deposit(
        signed,
        sender=hunter.account_id,
        escrow=ESCROW,
        token_id=settings.bounty_token,
        amount=5,
        msg=msg,
        memo="arsune:bond:c1",
    )
    assert escrow_fund_msg(info, "b1") == '{"fund":"b1"}'
    assert escrow_fund_msg({**info, "mode": "dry"}, "b1") is None
