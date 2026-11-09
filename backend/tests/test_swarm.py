"""Swarm agents: strategies, tip negotiation, wallets, bond signing, /state, and a full run
against an in-memory copy of Arsune's public API contract."""

import asyncio
import json
import random
import re
from collections import Counter

import httpx
import pytest
from app.bounty.claims import fingerprint as arsune_fingerprint
from app.settlement.base import SettlementError
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT
from app.settlement.near_intents import verify_signed_transfer
from app.settlement.swarm_accounts import plan_swarm_accounts, save_swarm_accounts
from swarm.arsune import BountyFeed
from swarm.findings import (
    broken_links,
    copy_of,
    guess,
    overflow,
    price_mismatches,
)
from swarm.identity import Identity, bond_memo, load_accounts, sign_payment
from swarm.market import (
    TipBoard,
    hunter_offer,
    max_share_bps,
    scout_accepts,
    success_chance,
)
from swarm.runner import Config, SwarmError, assign, check_mode, run
from swarm.site import Crawl, Page, SiteReader, canonical, read_links
from swarm.state import SwarmState, serve_state
from swarm.wallet import Record, Wallet, is_broke

TOKEN = "nep141:usdc.test"
SETTLEMENT = {
    "mode": "dry",
    "arsune_account": DRY_ACCOUNT,
    "token": TOKEN,
    "intents_contract": "intents.near",
    "salt": DRY_SALT,
}
STATE_KEYS = {"run_id", "mode", "bounty_id", "started_at", "finished", "agents", "tips", "log"}
AGENT_KEYS = {
    "id",
    "account",
    "role",
    "strategy",
    "state",
    "balance",
    "claims",
    "proven",
    "earned",
    "bonds_lost",
}
TIP_KEYS = {
    "id",
    "scout",
    "claim_type",
    "urls",
    "ask_share_bps",
    "status",
    "buyer",
    "final_share_bps",
    "note",
}


# ---------------------------------------------------------------- strategies and findings


def test_strategy_mix_is_proportional_and_complete():
    names = assign(30, {"links": 20, "mobile": 20, "careless": 10}, random.Random(1))
    assert len(names) == 30
    assert Counter(names) == {"links": 12, "mobile": 12, "careless": 6}
    assert assign(0, {"links": 1}, random.Random(1)) == []


def test_links_parse_like_a_browser_and_drop_query_urls():
    links, text = read_links(
        '<a href="gone#top">Gone</a><a href="/x/../site/a">A</a><a href="?q=1">Q</a>'
        '<a href="http://other.test/">Other</a><a href="/site/a">Again</a>',
        "http://site.test:80/site/",
    )
    assert links == ["http://site.test/site/gone", "http://site.test/site/a"]
    assert text["http://site.test/site/gone"] == "Gone"
    assert canonical("HTTP://Site.Test:8080/a/./b/../c") == "http://site.test:8080/a/c"
    assert canonical("http://site.test/a?x=1") is None


async def test_a_redirect_alias_is_named_by_its_final_url():
    def handler(request):
        if request.url.path == "/s/old":
            return httpx.Response(301, headers={"location": "/s/new"})
        links = '<a href="/s/old">old</a><a href="/s/new">new</a>'
        return httpx.Response(200, html=links if request.url.path == "/s/" else "<p>new</p>")

    reader = SiteReader(httpx.MockTransport(handler))
    crawl = Crawl(reader, "http://s.test/s/", 10, random.Random(1))
    while not crawl.done:
        await crawl.step()
    await reader.close()
    assert sorted(crawl.pages) == ["http://s.test/s/", "http://s.test/s/new"]


async def test_feed_marks_faults_that_are_claimed_or_proven():
    class Api:
        async def bounty(self, bounty_id):
            claims = [
                {"id": "1", "fingerprint": "a", "status": "checking", "hunter_account": "h1"},
                {
                    "id": "2",
                    "fingerprint": "b",
                    "outcome": "proven",
                    "status": "paid",
                    "settled_at": "t",
                    "hunter_account": "h2",
                },
                {
                    "id": "3",
                    "fingerprint": "c",
                    "outcome": "rejected",
                    "status": "rejected",
                    "settled_at": "t",
                    "hunter_account": "h1",
                },
            ]
            return {"status": "open", "claims": claims, "ledger": []}

    feed = BountyFeed(Api(), "b1")
    await feed.refresh()
    assert set(feed.claimed) == {"a", "b"} and set(feed.proven) == {"b"}
    assert [c["id"] for c in feed.claims_by("h1")] == ["1", "3"]


def test_broken_links_need_an_ok_source_and_an_error_target():
    page = Page("http://s.test/", 200, "<a>", ["http://s.test/a", "http://s.test/gone"])
    found = broken_links(page, {"http://s.test/a": 200, "http://s.test/gone": 410})
    assert [(f.claim_type, f.details) for f in found] == [
        ("broken_link", {"source": "http://s.test/", "target": "http://s.test/gone"})
    ]
    assert found[0].urls == ["http://s.test/", "http://s.test/gone"]
    assert broken_links(Page("http://s.test/", 500, "<a>", page.links), {}) == []


def test_overflow_uses_the_checker_tolerance():
    assert overflow("http://s.test/", 392) is None
    assert overflow("http://s.test/", None) is None
    found = overflow("http://s.test/", 393)
    assert found.details == {"url": "http://s.test/"}


def test_price_mismatch_needs_one_value_per_page_and_different_values():
    fact = lambda key, value: {"key": key, "value": value}
    found = price_mismatches(
        {
            "http://s.test/a": [fact("k", "19"), fact("same", "5")],
            "http://s.test/b": [fact("k", "29"), fact("same", "5")],
            "http://s.test/c": [fact("k", "19"), fact("k", "39")],  # two values: not usable
        }
    )
    assert [(f.urls, f.details) for f in found] == [
        (["http://s.test/a", "http://s.test/b"], {"key": "k"})
    ]


def test_fault_key_is_arsunes_fingerprint():
    found = overflow("http://s.test/p", 900)
    assert found.key == arsune_fingerprint(found.claim_type, found.urls, found.details)
    copied = copy_of(
        {"claim_type": "horizontal_overflow", "urls": found.urls, "details": found.details}
    )
    assert copied.key == found.key


def test_careless_guesses_are_marked_and_unmeasured():
    pages = [
        Page("http://s.test/", 200, '<div style="width:900px">', ["http://s.test/old-page"]),
    ]
    rng = random.Random(3)
    guesses = [guess(pages, rng) for _ in range(20)]
    assert all(g.guessed for g in guesses)
    assert {g.claim_type for g in guesses} == {"horizontal_overflow", "broken_link"}
    assert not any("scrollWidth" in " ".join(g.steps) for g in guesses)


# ---------------------------------------------------------------- negotiation


def test_tip_value_follows_the_hunters_own_record():
    fresh = max_share_bps(success_chance(0, 0), 1_000_000, 100_000, margin=0.4)
    proven = max_share_bps(success_chance(4, 4), 1_000_000, 100_000, margin=0.4)
    failing = max_share_bps(success_chance(0, 4), 1_000_000, 100_000, margin=0.4)
    assert failing == 0 < fresh < proven <= 5000
    assert fresh % 100 == 0


def test_offer_rules():
    assert hunter_offer(3000, 3500) == ("accept", 3000)
    assert hunter_offer(3000, 2000) == ("counter", 2000)
    assert hunter_offer(3000, 400) == ("walk", None)
    assert scout_accepts(2000, 1800) and not scout_accepts(1700, 1800)


class FakeScout:
    def __init__(self, floor):
        self.floor, self.asked = floor, []

    async def answer(self, tip, hunter, share):
        self.asked.append((hunter, share))
        return scout_accepts(share, self.floor)


def board_with_tip(ask=3000):
    log = []
    board = TipBoard(lambda agent, text: log.append(text))
    tip = board.post("scout-01", "scout.near", overflow("http://s.test/", 900), ask)
    return board, tip, log


async def test_hunter_accepts_an_ask_within_its_limit():
    board, tip, _ = board_with_tip(3000)
    assert await board.negotiate(tip, "hunter-01", 3500, "fine", FakeScout(0))
    assert (tip.status, tip.buyer, tip.final_share_bps) == ("sold", "hunter-01", 3000)
    assert not await board.negotiate(tip, "hunter-02", 5000, "late", FakeScout(0))


async def test_counter_once_then_scout_accepts():
    board, tip, log = board_with_tip(3000)
    scout = FakeScout(floor=1800)
    assert await board.negotiate(tip, "hunter-01", 2000, "its record is 1 of 3", scout)
    assert scout.asked == [("hunter-01", 2000)]
    assert (tip.status, tip.final_share_bps) == ("sold", 2000)
    assert any("countered" in line and "its record is 1 of 3" in line for line in log)


async def test_refused_counter_declines_and_cannot_be_repeated():
    board, tip, _ = board_with_tip(3000)
    scout = FakeScout(floor=2500)
    assert not await board.negotiate(tip, "hunter-01", 2000, "too risky", scout)
    assert (tip.status, tip.buyer, tip.final_share_bps) == ("declined", None, None)
    assert not await board.negotiate(tip, "hunter-01", 2400, "again", scout)
    assert scout.asked == [("hunter-01", 2000)]


async def test_walking_away_keeps_the_tip_for_others():
    board, tip, log = board_with_tip(3000)
    assert not await board.negotiate(tip, "hunter-01", 0, "no room", FakeScout(0))
    assert tip.status == "offered"
    assert board.offers_for("hunter-01") == []
    assert board.offers_for("hunter-02") == [tip]
    assert "walked away" in log[-1]


# ---------------------------------------------------------------- wallet


def row(id_, kind, amount, account="me", status="completed"):
    return {"id": id_, "kind": kind, "amount": amount, "account": account, "status": status}


def test_wallet_books_ledger_rows_once():
    wallet = Wallet("me", start=300_000)
    rows = [
        row("1", "bond_in", 100_000),
        row("2", "bond_forfeit", 100_000),
        row("3", "bond_in", 100_000),
        row("4", "reward_out", 700_000),
        row("5", "bond_refund", 100_000),
        row("6", "bond_in", 100_000, status="pending"),
        row("7", "reward_out", 1_000_000, account="other"),
    ]
    assert len(wallet.apply(rows)) == 5
    assert wallet.apply(rows) == []
    assert (wallet.balance, wallet.earned, wallet.bonds_lost) == (900_000, 700_000, 100_000)


def test_broke_only_without_bond_money_and_without_open_claims():
    wallet = Wallet("me", start=150_000)
    assert not is_broke(wallet, 100_000, pending_claims=0)
    wallet.reserved = 100_000
    assert not wallet.can_afford(100_000)
    assert not is_broke(wallet, 100_000, pending_claims=1)
    assert is_broke(wallet, 100_000, pending_claims=0)
    live = Wallet("me", start=10**9, live=True, live_balance=50_000)
    assert live.balance == 50_000 and is_broke(live, 100_000, 0)


def test_record_counts_outcomes_per_type():
    record = Record()
    for outcome in ["proven", "rejected", "duplicate"]:
        record.add("broken_link", outcome)
    assert (record.proven("broken_link"), record.resolved("broken_link")) == (1, 3)
    assert record.resolved("price_mismatch") == 0


# ---------------------------------------------------------------- signing


def test_bond_signature_is_bound_to_the_claim_and_amount():
    hunter = Identity.generate("hunter")
    assert re.fullmatch(r"[0-9a-f]{64}", hunter.account_id)
    claim_id = "0b3e5f0e-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
    signed = sign_payment(hunter, SETTLEMENT, 100_000, bond_memo(claim_id))
    intent = json.loads(signed["payload"]["message"])["intents"][0]
    assert intent["memo"] == f"arsune:bond:{claim_id}"
    assert intent["tokens"] == {TOKEN: "100000"}
    expected = {
        "sender": hunter.account_id,
        "receiver": DRY_ACCOUNT,
        "token_id": TOKEN,
        "contract": "intents.near",
    }
    verify_signed_transfer(signed, amount=100_000, memo=bond_memo(claim_id), **expected)
    other = "1b3e5f0e-1a2b-4c3d-8e4f-5a6b7c8d9e0f"
    with pytest.raises(SettlementError, match="memo"):
        verify_signed_transfer(signed, amount=100_000, memo=bond_memo(other), **expected)
    with pytest.raises(SettlementError, match="amount"):
        verify_signed_transfer(signed, amount=200_000, memo=bond_memo(claim_id), **expected)


def test_signing_never_fetches_a_salt():
    with pytest.raises(ValueError, match="salt"):
        sign_payment(Identity.generate("hunter"), {**SETTLEMENT, "salt": None}, 1, "m")


def test_live_accounts_come_from_the_swarm_accounts_file(tmp_path):
    roles = ["owner", "hunter-1", "hunter-2", "hunter-10", "scout-1"]
    path = save_swarm_accounts(plan_swarm_accounts("swarm.near", roles), tmp_path / "a.json")
    loaded = load_accounts(path)
    assert [(i.role, i.account_id) for i in loaded] == [
        ("owner", "owner.swarm.near"),
        ("hunter", "hunter-1.swarm.near"),
        ("hunter", "hunter-2.swarm.near"),
        ("hunter", "hunter-10.swarm.near"),
        ("scout", "scout-1.swarm.near"),
    ]
    signed = sign_payment(loaded[1], SETTLEMENT, 5, "memo")
    assert json.loads(signed["payload"]["message"])["signer_id"] == "hunter-1.swarm.near"


def test_mode_guards():
    check_mode(Config(), SETTLEMENT)
    with pytest.raises(SwarmError, match="dry"):
        check_mode(Config(), {"mode": "near_intents"})
    with pytest.raises(SwarmError, match="i-approve-mainnet"):
        check_mode(Config(live=True), {"mode": "near_intents"})
    with pytest.raises(SwarmError, match="near_intents"):
        check_mode(Config(live=True, approve_mainnet=True), SETTLEMENT)
    with pytest.raises(SwarmError, match="capped"):
        check_mode(
            Config(live=True, approve_mainnet=True, setup=True, budget=2_000_000),
            {"mode": "near_intents"},
        )


# ---------------------------------------------------------------- /state


async def test_state_endpoint_serves_the_exact_schema():
    state = SwarmState("run1", "simulation")
    state.board = TipBoard(state.log)
    state.board.post("scout-01", "scout.near", overflow("http://s.test/", 900), 3000)
    server = await serve_state(state, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"http://127.0.0.1:{port}/state")
            missing = await client.get(f"http://127.0.0.1:{port}/other")
    finally:
        server.close()
    body = response.json()
    assert response.headers["access-control-allow-origin"] == "*"
    assert set(body) == STATE_KEYS and missing.status_code == 404
    assert body["mode"] == "simulation" and body["finished"] is False
    assert set(body["tips"][0]) == TIP_KEYS
    assert set(body["log"][0]) == {"at", "agent", "text"}


def test_state_log_keeps_the_last_200_events():
    state = SwarmState("run1", "simulation")
    for n in range(250):
        state.log("swarm", f"event {n}")
    assert len(state.view()["log"]) == 200
    assert state.view()["log"][0]["text"] == "event 50"


# ---------------------------------------------------------------- a full run


SITE = {
    "/site/": '<a href="/site/a">A</a><a href="gone">Gone</a><a href="/site/old-page">Old</a>'
    '<a href="/site/b">B</a>',
    "/site/a": '<a href="/site/">Home</a><a href="/site/gone">Gone</a>',
    "/site/b": '<a href="/site/">Home</a><div style="width:900px">wide in markup only</div>',
    "/site/old-page": "<p>Still here.</p>",
}
ORIGIN = "http://site.test"
REAL = {
    arsune_fingerprint(
        "broken_link",
        [ORIGIN + src, ORIGIN + "/site/gone"],
        {"source": ORIGIN + src, "target": ORIGIN + "/site/gone"},
    )
    for src in ["/site/", "/site/a"]
}


def site_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "POST" and request.url.path == "/bounty-proof-control":
        return httpx.Response(200, json={})
    if request.url.path in SITE:
        return httpx.Response(200, html=SITE[request.url.path])
    return httpx.Response(404, html="<h1>Missing</h1>")


class FakeArsune:
    """Arsune's public bounty API in memory. Its checker knows the site's real faults."""

    def __init__(self, budget):
        self.budget, self.bounty, self.claims, self.ledger = budget, None, [], []
        self.funded = False

    def add_row(self, claim, kind, amount, account):
        self.ledger.append(
            {
                "id": str(len(self.ledger)),
                "claim_id": claim and claim["id"],
                "kind": kind,
                "amount": amount,
                "account": account,
                "status": "completed",
            }
        )

    def check(self):
        """A claim is checked on the read after the one that first showed it."""
        reward, bond = self.bounty["reward_amount"], self.bounty["bond_amount"]
        for claim in self.claims:
            if claim["status"] == "received":
                claim["status"] = "checking"
                continue
            if claim["status"] != "checking":
                continue
            proven = {c["fingerprint"] for c in self.claims if c["outcome"] == "proven"}
            if claim["fingerprint"] not in REAL:
                claim["outcome"] = "rejected"
                self.add_row(claim, "bond_forfeit", bond, claim["hunter_account"])
            elif claim["fingerprint"] in proven:
                claim["outcome"] = "duplicate"
                self.add_row(claim, "bond_refund", bond, claim["hunter_account"])
            elif self.budget < reward:
                claim["outcome"], claim["status"] = "proven", "needs_review"
                continue
            else:
                claim["outcome"] = "proven"
                scout = claim["scout"]
                part = reward * scout["share_bps"] // 10_000 if scout else 0
                self.add_row(claim, "reward_out", reward - part, claim["hunter_account"])
                self.add_row(claim, "bond_refund", bond, claim["hunter_account"])
                if part:
                    self.add_row(claim, "scout_share", part, scout["account"])
                self.budget -= reward
                if self.budget < reward:
                    self.bounty["status"] = "closed"
            claim["status"] = "paid" if claim["outcome"] == "proven" else claim["outcome"]
            claim["settled_at"] = "2026-11-09T00:00:00Z"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        if path == "/api/settlement":
            return httpx.Response(200, json=SETTLEMENT)
        if path == "/api/bounties" and method == "POST":
            self.bounty = {**body, "id": "b1", "status": "pending_proof", "proof_token": "t" * 20}
            self.bounty["url"] = body["url"]
            return httpx.Response(201, json=self.bounty)
        if path == "/api/bounties/b1/verify":
            self.bounty["status"] = "open"
            return httpx.Response(200, json=self.bounty)
        if path == "/api/bounties/b1/fund":
            memo = f"arsune:fund:{body['fund_id']}"
            verify_signed_transfer(
                body["signed_intent"],
                sender=body["sender"],
                receiver=DRY_ACCOUNT,
                token_id=TOKEN,
                amount=body["amount"],
                memo=memo,
            )
            self.funded = True
            return httpx.Response(200, json=self.bounty)
        if path == "/api/bounties/b1/claims":
            return self.claim(body)
        if path == "/api/bounties/b1":
            self.check()
            return httpx.Response(
                200, json={**self.bounty, "claims": self.claims, "ledger": self.ledger}
            )
        return httpx.Response(404, json={"detail": "Not found"})

    def claim(self, body):
        if self.bounty["status"] != "open":
            return httpx.Response(409, json={"detail": "The bounty is not open."})
        hunter = body["hunter_account"]
        if (
            sum(1 for c in self.claims if c["hunter_account"] == hunter and not c["settled_at"])
            >= 3
        ):
            return httpx.Response(429, json={"detail": "Too many unresolved claims."})
        try:
            verify_signed_transfer(
                body["bond_intent"],
                sender=hunter,
                receiver=DRY_ACCOUNT,
                token_id=TOKEN,
                amount=self.bounty["bond_amount"],
                memo=f"arsune:bond:{body['claim_id']}",
            )
        except SettlementError as exc:
            return httpx.Response(422, json={"detail": str(exc)})
        claim = {
            "id": body["claim_id"],
            "hunter_account": hunter,
            "claim_type": body["claim_type"],
            "urls": body["urls"],
            "details": body["details"],
            "fingerprint": arsune_fingerprint(body["claim_type"], body["urls"], body["details"]),
            "status": "received",
            "outcome": None,
            "checker_reason": "fake checker",
            "settled_at": None,
            "scout": body.get("scout"),
        }
        self.claims.append(claim)
        self.add_row(claim, "bond_in", self.bounty["bond_amount"], hunter)
        rows = [r for r in self.ledger if r["claim_id"] == claim["id"]]
        return httpx.Response(202, json={**claim, "ledger": rows})


def run_config(**overrides):
    values = {
        "api_url": "http://arsune.test",
        "site_url": ORIGIN + "/site/",
        "setup": True,
        "hunters": 9,
        "scouts": 2,
        "mix": {"links": 4, "careless": 3, "copycat": 2},
        "scout_mix": {"links": 1, "hasty": 1},
        "hunter_wallet": 200_000,
        "budget": 20_000_000,
        "pace": 0,
        "poll": 0.02,
        "duration": 30,
        "settle_timeout": 5,
        "state_port": 0,
        "seed": 7,
        "quiet": True,
    }
    return Config(**{**values, **overrides})


async def test_full_simulation_against_the_api_contract():
    arsune = FakeArsune(budget=20_000_000)
    summary = await asyncio.wait_for(
        run(
            run_config(),
            httpx.MockTransport(arsune.handler),
            httpx.MockTransport(site_handler),
        ),
        60,
    )
    assert arsune.funded
    outcomes = Counter(c["outcome"] for c in arsune.claims)
    assert outcomes["proven"] == len(REAL) == summary["proven"]
    assert summary["claims"] == len(arsune.claims) == sum(outcomes.values())
    assert summary["duplicate"] == outcomes["duplicate"] >= 1  # copycats and races
    assert summary["rejected"] == outcomes["rejected"] >= 1  # careless guesses
    assert summary["open"] == 0
    assert summary["hunter_rewards"] + summary["scout_shares"] == 2 * 1_000_000
    assert summary["bonds_lost"] == outcomes["rejected"] * 100_000
    # Every claimant signed its own bond; no hunter ever had more than three open claims.
    assert {c["hunter_account"] for c in arsune.claims} <= {
        r["account"] for r in arsune.ledger if r["kind"] == "bond_in"
    }


async def test_run_stops_when_the_bounty_closes():
    arsune = FakeArsune(budget=1_000_000)
    summary = await asyncio.wait_for(
        run(
            run_config(budget=1_000_000, mix={"links": 3}, scouts=0),
            httpx.MockTransport(arsune.handler),
            httpx.MockTransport(site_handler),
        ),
        60,
    )
    assert arsune.bounty["status"] == "closed"
    assert summary["proven"] == 1
    assert summary["wall_seconds"] < 30


async def test_run_stops_at_the_time_limit_and_settles_open_claims():
    arsune = FakeArsune(budget=20_000_000)
    summary = await asyncio.wait_for(
        run(
            # With real think time, four pages take longer than the 0.3 s limit.
            run_config(duration=0.3, pace=1.0, mix={"links": 2}, scouts=0),
            httpx.MockTransport(arsune.handler),
            httpx.MockTransport(site_handler),
        ),
        60,
    )
    assert summary["open"] == 0 and summary["wall_seconds"] < 10
    assert summary["claims"] == len(arsune.claims) < 4
