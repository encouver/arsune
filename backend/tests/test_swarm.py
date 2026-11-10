"""Swarm agents: strategies, tip negotiation, wallets, bond signing, /state, and a full run
against an in-memory copy of Arsune's public API contract."""

import asyncio
import json
import random
import re
from collections import Counter
from dataclasses import replace

import httpx
import pytest
from app.bounty.claims import fingerprint as arsune_fingerprint
from app.bounty.reputation import COUNTS, tier_for
from app.browser.checker import normalize_message as arsune_normalize
from app.settlement.base import SettlementError
from app.settlement.dry import DRY_ACCOUNT, DRY_SALT
from app.settlement.near_intents import verify_signed_transfer
from app.settlement.swarm_accounts import plan_swarm_accounts, save_swarm_accounts
from swarm.agents import SCOUT_FINDERS, STRATEGIES, Hunter, Scout, Swarm
from swarm.ai import AiHunter, AiScout, Seen, scripted_findings
from swarm.arsune import ApiError, BountyFeed
from swarm.findings import (
    broken_image,
    broken_links,
    claim_label,
    copy_of,
    guess,
    overflow,
    page_error,
    price_mismatches,
)
from swarm.identity import Identity, bond_memo, load_accounts, sign_payment
from swarm.llm import (
    AiBudget,
    BudgetSpent,
    ModelClient,
    ModelFailure,
    ModelSettings,
    model_settings,
)
from swarm.market import (
    TipBoard,
    hunter_offer,
    max_share_bps,
    scout_accepts,
    success_chance,
)
from swarm.runner import (
    DEFAULT_MIX,
    DEFAULT_SCOUT_MIX,
    Config,
    SwarmError,
    assign,
    check_mode,
    run,
    start_ai,
)
from swarm.site import BrowserPool, Crawl, Page, SiteReader, canonical, read_links
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
STATE_KEYS = {
    "run_id",
    "mode",
    "bounty_id",
    "started_at",
    "finished",
    "agents",
    "tips",
    "log",
    "ai",
}
AI_KEYS = {"agents", "calls", "usd_spent", "budget_usd", "model"}
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
    "tier",
    "required_bond",
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


def test_default_mix_has_a_specialist_for_each_of_the_five_claim_types():
    checks = {"links", "mobile", "prices", "images", "errors"}
    assert sum(DEFAULT_MIX.values()) == 100 and sum(DEFAULT_SCOUT_MIX.values()) == 10
    assert all(DEFAULT_MIX[name] > 0 and DEFAULT_SCOUT_MIX[name] > 0 for name in checks)
    assert all(STRATEGIES[name].finders == {name} for name in checks)
    assert all(SCOUT_FINDERS[name] == {name} for name in checks)
    assert STRATEGIES["generalist"].finders == checks


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
    assert body["ai"] == {"agents": 0, "calls": 0, "usd_spent": 0.0, "budget_usd": 0.0, "model": ""}
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
    """Arsune's public bounty API in memory. Its checker knows the site's real faults, and
    the bond follows the hunter's record with Arsune's own tier rule."""

    def __init__(self, budget):
        self.budget, self.bounty, self.claims, self.ledger = budget, None, [], []
        self.funded = False
        self.quotes = 0

    def quote(self, hunter):
        decided = Counter(
            c["outcome"] for c in self.claims if c["hunter_account"] == hunter and c["settled_at"]
        )
        tier, bps = tier_for({name: decided[name] for name in COUNTS})
        bond = -(-self.bounty["bond_amount"] * bps // 10_000)
        return {"hunter": hunter, "tier": tier, "required_bond": bond, "multiplier": bps / 10_000}

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
        reward = self.bounty["reward_amount"]
        for claim in self.claims:
            bond = claim["bond"]
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
        if path == "/api/bounties/b1/bond":
            self.quotes += 1
            return httpx.Response(200, json=self.quote(request.url.params["hunter"]))
        if path == "/api/bounties/b1":
            self.check()
            accounts = dict.fromkeys(c["hunter_account"] for c in self.claims)
            hunters = [self.quote(account) | {"account": account} for account in accounts]
            return httpx.Response(
                200,
                json={
                    **self.bounty,
                    "claims": self.claims,
                    "ledger": self.ledger,
                    "hunters": hunters,
                },
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
        bond = self.quote(hunter)["required_bond"]
        try:
            verify_signed_transfer(
                body["bond_intent"],
                sender=hunter,
                receiver=DRY_ACCOUNT,
                token_id=TOKEN,
                amount=bond,
                memo=f"arsune:bond:{body['claim_id']}",
            )
        except SettlementError as exc:
            rule = f"The bond for {hunter} must be exactly {bond} token units."
            return httpx.Response(422, json={"detail": f"{exc}. {rule}"})
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
            "bond": bond,
        }
        self.claims.append(claim)
        self.add_row(claim, "bond_in", bond, hunter)
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
    assert summary["bonds_lost"] == sum(
        c["bond"] for c in arsune.claims if c["outcome"] == "rejected"
    )
    assert arsune.quotes >= 9  # every hunter asked for its own bond
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


# ---------------------------------------------------------------- reputation bonds


class QuoteApi:
    """Bond quotes in order, and a claim intake that first loses a race on the bond."""

    def __init__(self, quotes, race=False):
        self.quotes, self.race, self.signed = list(quotes), race, []

    async def bond_quote(self, bounty_id, hunter):
        quote = self.quotes.pop(0) if len(self.quotes) > 1 else self.quotes[0]
        return {"tier": quote[0], "required_bond": quote[1]}

    async def settlement(self):
        return SETTLEMENT

    async def submit_claim(self, bounty_id, body):
        amount = json.loads(body["bond_intent"]["payload"]["message"])["intents"][0]["tokens"]
        self.signed.append(int(amount[TOKEN]))
        if self.race:
            self.race = False
            raise ApiError(422, "The hunter's reputation changed. The bond must be exactly 50000.")
        return {"id": body["claim_id"], "ledger": []}


def lone_hunter(api, cls=Hunter, wallet=300_000, **swarm_args):
    log = []
    swarm = Swarm(
        api,
        BountyFeed(api, "b1"),
        None,
        None,
        TipBoard(lambda agent, text: log.append(text)),
        lambda agent, text: log.append(text),
        SETTLEMENT,
        {"id": "b1", "reward_amount": 1_000_000, "bond_amount": 100_000, "url": ORIGIN + "/site/"},
        pace=0,
        **swarm_args,
    )
    strategy = "ai" if cls is AiHunter else "links"
    hunter = cls(
        swarm, "hunter-ai-01", Identity.generate("hunter"), strategy, random.Random(1), wallet
    )
    swarm.agents.append(hunter)
    return swarm, hunter, log


async def test_a_bond_race_is_quoted_again_and_retried_once():
    api = QuoteApi([("new", 100_000), ("trusted", 50_000)], race=True)
    _, hunter, log = lone_hunter(api)
    await hunter.claim(overflow("http://site.test/site/b", 900))
    assert api.signed == [100_000, 50_000]
    assert hunter.claims_made == 1 and hunter.bond == 50_000
    assert "hunter-ai-01 is now trusted: its bond drops to 0.05 USDC." in log
    assert log[-1].endswith("with a 0.05 USDC bond.")


async def test_a_hunter_that_cannot_pay_its_own_bond_keeps_the_finding_and_goes_broke():
    api = QuoteApi([("very_poor", 300_000)])
    _, hunter, log = lone_hunter(api, wallet=200_000)
    finding = overflow("http://site.test/site/b", 900)
    await hunter.claim(finding)
    assert api.signed == [] and hunter.queue == [(finding, None)]
    assert hunter.finished() and hunter.state == "broke"
    assert "its bond costs 0.3 USDC" in log[-1]


def test_tier_changes_are_plain_sentences():
    _, hunter, log = lone_hunter(QuoteApi([("new", 100_000)]))
    hunter.set_bond("new", 100_000)
    hunter.set_bond("standard", 100_000)
    hunter.set_bond("very_poor", 300_000)
    hunter.set_bond("bogus", "300000")  # not a valid quote: ignored
    assert log == [
        "hunter-ai-01 is now standard: its bond stays at 0.1 USDC.",
        "hunter-ai-01 is now very poor: its bond rises to 0.3 USDC.",
    ]
    assert hunter.view()["tier"] == "very_poor" and hunter.view()["required_bond"] == 300_000


async def test_a_scout_that_finishes_while_it_weighs_a_counter_stays_done():
    swarm, _, _ = lone_hunter(QuoteApi([("new", 100_000)]))
    scout = Scout(swarm, "scout-01", Identity.generate("scout"), "links", random.Random(1))
    scout.post(overflow(PLAN, 900), 3000)

    async def weigh(tip, hunter, share):
        scout.state = "done"  # its loop ended during a slow answer, such as a model call
        return True, ""

    scout.state, scout.weigh = "searching", weigh
    assert await scout.answer(scout.tips[0], "hunter-01", 2000)
    assert scout.state == "done" and not swarm.scouts_searching()


# ---------------------------------------------------------------- AI agents

AI_MODEL = ModelSettings("http://model.test/v1", "sk-test-secret-key", "fake-model", 1.0, 4.0)


class FakeModel:
    """An OpenAI-compatible chat endpoint. `reply(system, user)` returns the message text."""

    def __init__(self, reply, usage=(1000, 200)):
        self.reply, self.usage, self.requests = reply, usage, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer sk-test-secret-key"
        self.requests.append(body)
        system, user = (m["content"] for m in body["messages"])
        content = self.reply(system, user)
        if isinstance(content, httpx.Response):
            return content
        usage = {"prompt_tokens": self.usage[0], "completion_tokens": self.usage[1]}
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}], "usage": usage}
        )

    def client(self, limit=0.5):
        budget = AiBudget(limit, AI_MODEL.input_usd_per_million, AI_MODEL.output_usd_per_million)
        return ModelClient(AI_MODEL, budget, httpx.MockTransport(self.handler))


def seen(url, statuses=None, width=390, facts=(), links=None):
    page = Page(url, 200, "<main>Plans</main>", list(statuses or {}), links or {})
    return Seen(page, dict(statuses or {}), width, list(facts), "Plan", "Plans")


PLAN, LISTING = ORIGIN + "/site/p/hosting", ORIGIN + "/site/c/hosting"
PRICE = {"key": "visible-price|abc", "value": "USD 29", "context": None}


def ai_hunter(model, limit=0.5):
    swarm, hunter, log = lone_hunter(QuoteApi([("new", 100_000)]), AiHunter, ai=model.client(limit))
    hunter.notebook.add(seen(LISTING, facts=[{**PRICE, "value": "USD 19"}]))
    hunter.notebook.add(seen(PLAN, {ORIGIN + "/site/gone": 404}, width=640, facts=[PRICE]))
    return swarm, hunter, log


def test_model_settings_fall_back_to_arsunes_model_settings(monkeypatch):
    for name, value in {
        "ARSUNE_MODEL_BASE_URL": "https://cloud-api.near.ai/v1",
        "ARSUNE_MODEL_API_KEY": "k1",
        "ARSUNE_MODEL_NAME": "shared-model",
        "ARSUNE_MODEL_INPUT_USD_PER_MILLION": "0.2",
        "ARSUNE_MODEL_OUTPUT_USD_PER_MILLION": "1.2",
        "ARSUNE_SWARM_MODEL_NAME": "swarm-model",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("ARSUNE_SWARM_MODEL_API_KEY", raising=False)
    settings = model_settings(env_file=None)
    assert (settings.base_url, settings.api_key, settings.name) == (
        "https://cloud-api.near.ai/v1",
        "k1",
        "swarm-model",
    )
    assert (settings.input_usd_per_million, settings.output_usd_per_million) == (0.2, 1.2)
    assert settings.problem() is None and "k1" not in repr(settings)


def test_ai_agents_refuse_to_start_without_token_prices():
    unpriced = ModelSettings("https://m.test/v1", "k", "m", None, 1.0)
    with pytest.raises(SwarmError, match="token prices"):
        start_ai(Config(ai_hunters=1, ai_model=unpriced))
    with pytest.raises(SwarmError, match="ai-budget"):
        start_ai(Config(ai_scouts=1, ai_model=AI_MODEL, ai_budget=50))
    assert start_ai(Config(ai_model=unpriced)) is None  # no AI agents, no model needed


async def test_budget_charges_reported_usage_and_refuses_a_call_it_cannot_pay():
    model = FakeModel(lambda system, user: '{"claims": []}')
    client = model.client(limit=0.0101)
    assert await client.ask("s", "u", 1000) == {"claims": []}
    assert client.budget.spent == pytest.approx((1000 * 1.0 + 200 * 4.0) / 1e6)
    client.budget.spent = 0.01  # the next worst case (1000 output tokens) does not fit
    with pytest.raises(BudgetSpent):
        await client.ask("s", "u", 1000)
    assert len(model.requests) == 1 and client.budget.exhausted
    await client.close()


async def test_a_failed_call_is_no_action_and_keeps_its_reservation():
    model = FakeModel(lambda system, user: httpx.Response(500, json={"error": "down"}))
    client = model.client()
    with pytest.raises(ModelFailure, match="HTTP 500"):
        await client.ask("s", "u", 100)
    assert client.budget.spent == 0 and client.budget.held == 0  # an error answer is not billed
    await client.close()


async def test_a_valid_answer_becomes_a_claim_with_the_models_reason():
    why = "the plan page shows USD 29 but the listing shows USD 19"
    answer = {
        "claims": [{"type": "price_mismatch", "pages": ["p2", "p1"], "fact": "F1", "why": why}],
        "tips": [],
    }
    model = FakeModel(lambda system, user: "```json\n" + json.dumps(answer) + "\n```")
    swarm, hunter, log = ai_hunter(model)
    await hunter.consult([])
    [(finding, tip)] = hunter.queue
    assert (finding.claim_type, finding.details, tip) == (
        "price_mismatch",
        {"key": "visible-price|abc"},
        None,
    )
    assert finding.urls == [PLAN, LISTING]
    assert finding.key == arsune_fingerprint("price_mismatch", finding.urls, finding.details)
    assert why in finding.explanation
    assert f"hunter-ai-01: {why}; claiming price_mismatch" in log
    system, user = (m["content"] for m in model.requests[0]["messages"])
    assert "p2 /site/p/hosting" in user and 'links that fail: L1 /site/gone "" HTTP 404' in user
    assert "phone width 640 px" in user and "also seen: p1 /site/c/hosting USD 19" in user
    # The system prompt is the same for every AI hunter; the agent's name is in the user text.
    assert user.startswith("You are hunter-ai-01.") and "hunter-ai-01" not in system
    assert hunter.identity.private_key not in json.dumps(model.requests)
    assert swarm.ai.budget.calls == 1 and not hunter.notebook.unreviewed
    await swarm.ai.close()


def test_a_free_local_model_is_allowed_and_never_spends_the_budget():
    free = ModelSettings("http://127.0.0.1:11434/v1", "ollama", "llama3:latest", 0.0, 0.0)
    assert free.problem() is None
    assert "token prices" in ModelSettings("http://m.test/v1", "k", "m", -1.0, 0.0).problem()
    budget = AiBudget(0.01, 0.0, 0.0)
    for _ in range(100):
        budget.settle(budget.reserve(50_000, 1200), None)
    assert budget.spent == 0 and budget.calls == 100 and not budget.exhausted


def test_timeout_and_temperature_come_from_the_swarms_own_settings(monkeypatch):
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_TIMEOUT_SECONDS", "300")
    monkeypatch.setenv("ARSUNE_SWARM_MODEL_TEMPERATURE", "0.2")
    settings = model_settings(env_file=None)
    assert (settings.timeout_seconds, settings.temperature) == (300, 0.2)
    monkeypatch.delenv("ARSUNE_SWARM_MODEL_TIMEOUT_SECONDS")
    monkeypatch.delenv("ARSUNE_SWARM_MODEL_TEMPERATURE")
    settings = model_settings(env_file=None)
    assert (settings.timeout_seconds, settings.temperature) == (60, None)


async def test_requests_ask_for_json_and_send_a_temperature_only_when_set():
    model = FakeModel(lambda system, user: '{"claims": []}')
    cold = model.client()
    tuned = replace(AI_MODEL, temperature=0.2, timeout_seconds=300)
    warm = ModelClient(tuned, cold.budget, httpx.MockTransport(model.handler))
    await cold.ask("s", "u", 100)
    await warm.ask("s", "u", 100)
    first, second = model.requests
    assert first["response_format"] == {"type": "json_object"} and "temperature" not in first
    assert second["temperature"] == 0.2 and second["response_format"] == first["response_format"]
    assert cold.http.timeout.read == 60 and warm.http.timeout.read == 300
    await cold.close()
    await warm.close()


async def test_an_id_with_its_label_attached_still_names_what_the_agent_saw():
    why = "/site/c/hosting lists USD 19, /site/p/hosting USD 29."
    claim = {
        "type": "price_mismatch",
        "pages": ["p1", "p2 /site/p/hosting"],
        "fact": "F1 hosting (USD per month)",
        "why": why,
    }
    model = FakeModel(lambda system, user: json.dumps({"claims": [claim], "tips": []}))
    swarm, hunter, log = ai_hunter(model)
    await hunter.consult([])
    [(finding, _)] = hunter.queue
    assert finding.urls == [LISTING, PLAN] and finding.details == {"key": PRICE["key"]}
    assert f"hunter-ai-01: {why[:-1]}; claiming price_mismatch" in log  # no ".;" in the log
    notebook = hunter.notebook
    assert notebook.value("F", " F1: USD 29") == PRICE["key"]
    assert notebook.value("F", "F12") is None and notebook.value("F", "fact F1") is None
    assert notebook.value("L", "F1") is None and notebook.value("F", None) is None
    await swarm.ai.close()


async def test_invalid_json_is_no_action():
    model = FakeModel(lambda system, user: "I would claim the broken link on p2.")
    swarm, hunter, log = ai_hunter(model)
    await hunter.consult([])
    assert hunter.queue == [] and not hunter.scripted
    assert log[-1] == "hunter-ai-01 takes no action on 2 pages: the answer is not valid JSON."
    await swarm.ai.close()


async def test_ids_the_agent_never_observed_are_dropped():
    claims = [
        {"type": "broken_link", "page": "p9", "link": "L1", "why": "made up"},
        {"type": "broken_link", "page": "p1", "link": "L1", "why": "link is not on p1"},
        {"type": "horizontal_overflow", "page": "p2", "why": "640 px wide at 390 px"},
        {"type": "sql_injection", "page": "p2", "why": "not provable"},
    ]
    model = FakeModel(lambda system, user: json.dumps({"claims": claims}))
    swarm, hunter, log = ai_hunter(model)
    await hunter.consult([])
    assert [(f.claim_type, f.urls) for f, _ in hunter.queue] == [("horizontal_overflow", [PLAN])]
    assert "hunter-ai-01 ignored 3 items in its model's answer: invalid, or more than 10." in log
    await swarm.ai.close()


async def test_a_spent_budget_turns_the_agent_scripted_without_a_call():
    model = FakeModel(lambda system, user: '{"claims": []}')
    swarm, hunter, log = ai_hunter(model, limit=1e-9)
    hunter.crawl.frontier.clear()  # the crawl is over, so the two pages go to the model
    assert await hunter.act()
    assert model.requests == [] and hunter.scripted
    assert {f.claim_type for f, _ in hunter.queue} == {
        "broken_link",
        "horizontal_overflow",
        "price_mismatch",
    }
    assert "the run's AI budget is spent" in log[0]
    await swarm.ai.close()


class CounterScout(FakeScout):
    def counts(self):
        return 0, 0


async def test_an_ai_hunter_counters_with_the_models_share_and_reason():
    reply = {
        "claims": [],
        "tips": [
            {
                "tip": "T1",
                "action": "counter",
                "share": 20,
                "why": "scout-01 has no proven tips yet",
            }
        ],
    }
    model = FakeModel(lambda system, user: json.dumps(reply))
    swarm, hunter, log = ai_hunter(model)
    hunter.notebook.unreviewed.clear()
    hunter.crawl.frontier.clear()
    scout = CounterScout(floor=1500)
    scout.id = "scout-01"
    swarm.agents.append(scout)
    tip = swarm.board.post("scout-01", "scout.near", overflow(PLAN, 900), 3000)
    claimed = []

    async def claim(finding, tip=None):
        claimed.append(tip)

    hunter.claim = claim
    assert await hunter.act()  # a fresh offer is worth a model call
    assert (
        "T1 from scout-01 (0 proven of 0 sold tips): mobile overflow, asks 30%"
        in (model.requests[0]["messages"][1]["content"])
    )
    assert scout.asked == [("hunter-ai-01", 2000)]
    assert (tip.status, tip.final_share_bps, claimed) == ("sold", 2000, [tip])
    assert any("because scout-01 has no proven tips yet" in line for line in log)
    assert hunter.fresh_offers() == []
    await swarm.ai.close()


def ai_scout(model):
    swarm, _, log = lone_hunter(QuoteApi([("new", 100_000)]), ai=model.client())
    scout = AiScout(swarm, "scout-ai-01", Identity.generate("scout"), random.Random(2))
    swarm.agents.append(scout)
    scout.notebook.add(seen(LISTING, facts=[{**PRICE, "value": "USD 19"}]))
    scout.notebook.add(seen(PLAN, {ORIGIN + "/site/gone": 404}, facts=[PRICE]))
    return swarm, scout, log


async def test_an_ai_scout_sells_at_the_models_ask_and_refuses_a_low_counter():
    replies = [
        {
            "tips": [
                {
                    "type": "broken_link",
                    "page": "p2",
                    "link": "L1",
                    "ask": 35,
                    "why": "/site/gone answers 404",
                }
            ]
        },
        {"action": "refuse", "why": "a 404 the checker will see is worth more than 15%"},
    ]
    model = FakeModel(lambda system, user: json.dumps(replies.pop(0)))
    swarm, scout, log = ai_scout(model)
    await scout.consult()
    [tip] = scout.tips
    assert (tip.ask_share_bps, tip.finding.evidence) == (3500, "/site/gone answers 404")
    assert "offered a broken link tip for 35% of the reward (/site/gone answers 404)" in log[-1]
    assert not await swarm.board.negotiate(tip, "hunter-01", 1500, "its record is 0 of 0", scout)
    assert tip.status == "declined" and scout.relist  # offered once more at a lower ask
    assert "counter-offer" in model.requests[1]["messages"][0]["content"]
    assert (
        "hunter-01 offers 15%: its record is 0 of 0" in model.requests[1]["messages"][1]["content"]
    )
    assert log[-1] == (
        "scout-ai-01 refused 15% from hunter-01: a 404 the checker will see is worth more than 15%."
    )
    await swarm.ai.close()


def smart_model(system: str, user: str) -> str:
    """Claims or sells every link the prompt shows as failing and not claimed; accepts tips."""
    if "counter-offer" in system:
        return json.dumps({"action": "accept", "why": "a proven 404 at this share still pays"})
    found, page = [], None
    for line in user.splitlines():
        if re.match(r"p\d+ ", line):
            page = line.split()[0]
        for link, status in re.findall(r'(L\d+) \S+ "[^"]*" HTTP (\d+)\b(?! \(claimed)', line):
            why = f"the link {link} answers HTTP {status}"
            found.append({"type": "broken_link", "page": page, "link": link, "ask": 30, "why": why})
    if "AI scout" in system:
        return json.dumps({"tips": found, "note": "no failing links"})
    tips = [
        {"tip": t, "action": "accept", "why": "a 404 tip"}
        for t in re.findall(r"^(T\d+) ", user, re.MULTILINE)
    ]
    return json.dumps({"claims": found, "tips": tips, "note": "every link answers"})


async def test_full_simulation_with_ai_agents(monkeypatch):
    async def unmeasured(self, url):
        return None

    monkeypatch.setattr(BrowserPool, "page_width", unmeasured)
    monkeypatch.setattr(BrowserPool, "price_facts", unmeasured)
    monkeypatch.setattr(BrowserPool, "desktop_look", unmeasured)
    arsune, model = FakeArsune(budget=20_000_000), FakeModel(smart_model)
    summary = await asyncio.wait_for(
        run(
            run_config(
                mix={"careless": 1},
                scout_mix={"links": 1},
                hunters=1,
                scouts=1,
                ai_hunters=2,
                ai_scouts=1,
                ai_model=AI_MODEL,
            ),
            httpx.MockTransport(arsune.handler),
            httpx.MockTransport(site_handler),
            httpx.MockTransport(model.handler),
        ),
        60,
    )
    ai = summary["ai"]
    assert set(ai) >= AI_KEYS and ai["agents"] == 3 and ai["model"] == "fake-model"
    assert ai["calls"] == len(model.requests) > 0
    assert ai["usd_spent"] == pytest.approx(ai["calls"] * (1000 * 1.0 + 200 * 4.0) / 1e6)
    assert ai["claims"] >= 1 and ai["rejected"] == 0  # the model claimed only real 404s
    systems = [r["messages"][0]["content"] for r in model.requests]
    assert any("AI scout in a bug-bounty swarm" in text for text in systems)
    assert any("AI bug-bounty hunter" in text for text in systems)
    assert summary["proven"] == len(REAL)
    assert "sk-test-secret-key" not in json.dumps(summary)


# ---------------------------------------------------------------- broken images and page errors

GALLERY, IMG = ORIGIN + "/site/gallery", ORIGIN + "/site/img/dashboard.png"


def test_new_claim_types_have_labels_and_unknown_types_a_readable_default():
    assert claim_label("broken_image") == "broken image"
    assert claim_label("page_error") == "page error"
    assert claim_label("console_warning") == "console warning"
    assert copy_of({"claim_type": "console_warning", "urls": [PLAN], "details": {}}).label == (
        "console warning"
    )


async def test_a_scripted_hunter_values_a_tip_of_a_new_claim_type():
    swarm, hunter, log = lone_hunter(QuoteApi([("new", 100_000)]))
    scout = CounterScout(floor=4000)
    scout.id = "scout-01"
    swarm.agents.append(scout)
    tip = swarm.board.post(
        "scout-01", "scout.near", page_error(GALLERY, "Error: boom happened"), 3000
    )
    await hunter.negotiate(tip)
    assert tip.status == "declined"
    assert any("its record for page error claims is 0 proven of 0" in line for line in log)


def test_a_page_error_is_keyed_like_arsune_keys_it():
    found = page_error(GALLERY, "TypeError:  Cannot read properties of NULL")
    message = arsune_normalize("TypeError:  Cannot read properties of NULL")
    assert found.details == {"page": GALLERY, "message": message}
    assert found.urls == [GALLERY]
    assert found.key == arsune_fingerprint("page_error", [GALLERY], found.details)
    image = broken_image(GALLERY, IMG, 404)
    assert image.details == {"page": GALLERY, "image": IMG} and image.urls == [GALLERY]


def gallery_seen():
    page = seen(GALLERY)
    page.images = [
        {"url": IMG, "shown": True, "status": 404},
        {"url": ORIGIN + "/site/img/old-banner.png", "shown": False, "status": 404},
    ]
    page.errors = [
        {"message": "Error: Gallery lightbox could not find its images", "first_party": True},
        {"message": "Error: Chat widget failed to start", "first_party": False},
    ]
    return page


async def test_an_ai_hunter_claims_a_broken_image_and_a_page_error():
    claims = [
        {"type": "broken_image", "page": "p3", "image": "I1", "why": "dashboard.png answers 404"},
        {"type": "page_error", "page": "p3", "error": "E1", "why": "the site's own script throws"},
        {"type": "page_error", "page": "p1", "error": "E1", "why": "E1 is not on p1"},
    ]
    model = FakeModel(lambda system, user: json.dumps({"claims": claims}))
    swarm, hunter, log = ai_hunter(model)
    hunter.notebook.add(gallery_seen())
    await hunter.consult([])
    user = model.requests[0]["messages"][1]["content"]
    assert "images that do not load: I1 /site/img/dashboard.png HTTP 404 shown" in user
    assert "I2 /site/img/old-banner.png HTTP 404 hidden" in user
    assert 'E1 "Error: Gallery lightbox could not find its images"' in user
    assert 'E2 "Error: Chat widget failed to start" from another origin\'s script' in user
    assert [(f.claim_type, f.details) for f, _ in hunter.queue] == [
        ("broken_image", {"page": GALLERY, "image": IMG}),
        (
            "page_error",
            {"page": GALLERY, "message": "error: gallery lightbox could not find its images"},
        ),
    ]
    assert "hunter-ai-01 ignored 1 items in its model's answer: invalid, or more than 10." in log
    await swarm.ai.close()


def test_scripted_checks_skip_hidden_images_and_other_origins_errors():
    found = scripted_findings(gallery_seen(), {})
    assert [(f.claim_type, f.details) for f in found] == [
        ("broken_image", {"page": GALLERY, "image": IMG}),
        (
            "page_error",
            {"page": GALLERY, "message": "error: gallery lightbox could not find its images"},
        ),
    ]
