"""Hunters and scouts. Each agent has its own key, wallet, crawl and record, and reaches Arsune
only through the public HTTP API. They share transports (HTTP pool, one Chromium, one reader of
the public bounty page) but never each other's private state.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from uuid import uuid4

import httpx

from swarm.arsune import ApiError, ArsuneApi, BountyFeed, claim_result
from swarm.findings import (
    LABELS,
    Finding,
    broken_links,
    copy_of,
    guess,
    overflow,
    path,
    price_mismatches,
)
from swarm.identity import Identity, bond_memo, sign_payment
from swarm.market import (
    MIN_COUNTER_BPS,
    STEP_BPS,
    Tip,
    TipBoard,
    max_share_bps,
    percent,
    scout_accepts,
    success_chance,
)
from swarm.site import BrowserPool, Crawl, Page, SiteReader
from swarm.state import usdc
from swarm.wallet import Record, Wallet, is_broke

logger = logging.getLogger(__name__)

MAX_UNRESOLVED = 3  # Arsune's limit of open claims per hunter and bounty
MAX_BOND_FAILURES = 3
CARELESS_GUESSES = 15


@dataclass(frozen=True)
class Strategy:
    finders: frozenset
    page_share: float  # part of the page budget this strategy walks
    buys_tips: bool
    margin: tuple[float, float]  # expected value a tip must leave, as a part of the reward


STRATEGIES = {
    "links": Strategy(frozenset({"links"}), 1.0, True, (0.45, 0.65)),
    "mobile": Strategy(frozenset({"mobile"}), 1.0, True, (0.45, 0.65)),
    "prices": Strategy(frozenset({"prices"}), 1.0, True, (0.45, 0.65)),
    "generalist": Strategy(frozenset({"links", "mobile", "prices"}), 0.6, True, (0.35, 0.55)),
    "careless": Strategy(frozenset(), 0.15, False, (0.0, 0.0)),
    "copycat": Strategy(frozenset(), 0.0, False, (0.0, 0.0)),
}
SCOUT_FINDERS = {
    "links": frozenset({"links"}),
    "mobile": frozenset({"mobile"}),
    "prices": frozenset({"prices"}),
    "hasty": frozenset(),
}


@dataclass
class Swarm:
    """Shared handles. Everything here is public information or plain transport."""

    api: ArsuneApi
    feed: BountyFeed
    site: SiteReader
    browser: BrowserPool
    board: TipBoard
    log: object  # log(agent_id, text)
    settlement: dict
    bounty: dict
    live: bool = False
    pace: float = 1.0
    copy_loss_limit: int = 3
    stop: asyncio.Event = field(default_factory=asyncio.Event)
    agents: list = field(default_factory=list)
    names: dict = field(default_factory=dict)  # account -> agent id, for readable sentences

    @property
    def bounty_id(self) -> str:
        return self.bounty["id"]

    @property
    def reward(self) -> int:
        return self.bounty["reward_amount"]

    @property
    def bond(self) -> int:
        return self.bounty["bond_amount"]

    @property
    def site_url(self) -> str:
        return self.bounty["url"]

    def name(self, account: str) -> str:
        return self.names.get(account) or account[:12]

    def agent(self, agent_id: str):
        return next(a for a in self.agents if a.id == agent_id)

    def scouts_searching(self) -> bool:
        return any(a.role == "scout" and a.state == "searching" for a in self.agents)

    def buyers_active(self) -> bool:
        return any(getattr(a, "buys_tips", False) and a.active for a in self.agents)

    def finders_active(self) -> bool:
        return any(a.role == "hunter" and a.strategy != "copycat" and a.active for a in self.agents)


async def inspect(swarm: Swarm, crawl: Crawl, facts: dict, finders, page: Page) -> list[Finding]:
    """Run an agent's own checks on one page it fetched."""
    found = []
    if "links" in finders:
        statuses = {target: await crawl.status_of(target) for target in page.links}
        found += broken_links(page, statuses)
    if "mobile" in finders:
        found += filter(None, [overflow(page.url, await swarm.browser.page_width(page.url))])
    if "prices" in finders:
        page_facts = await swarm.browser.price_facts(page.url)
        if page_facts:
            facts[page.url] = page_facts
            found += [f for f in price_mismatches(facts) if page.url in f.urls]
    return found


class Agent:
    role = "agent"

    def __init__(
        self, swarm: Swarm, agent_id: str, identity: Identity, strategy: str, rng, start=0
    ):
        self.swarm, self.id, self.identity, self.strategy, self.rng = (
            swarm,
            agent_id,
            identity,
            strategy,
            rng,
        )
        self.state = "searching"
        self.wallet = Wallet(identity.account_id, start, live=swarm.live)
        self._version = -1
        swarm.names[identity.account_id] = agent_id

    @property
    def account(self) -> str:
        return self.identity.account_id

    @property
    def active(self) -> bool:
        return self.state not in {"broke", "done"}

    def counts(self) -> tuple[int, int]:
        return 0, 0

    def view(self) -> dict:
        claims, proven = self.counts()
        return {
            "id": self.id,
            "account": self.account,
            "role": self.role,
            "strategy": self.strategy,
            "state": self.state,
            "balance": self.wallet.balance,
            "claims": claims,
            "proven": proven,
            "earned": self.wallet.earned,
            "bonds_lost": self.wallet.bonds_lost,
        }

    def log(self, text: str):
        self.swarm.log(self.id, text)

    async def think(self, low: float, high: float):
        await asyncio.sleep(self.rng.uniform(low, high) * self.swarm.pace)

    async def read_balance(self):
        """Live mode: the real USDC balance of this account inside intents.near."""
        if not self.swarm.live:
            return
        from app.settlement.base import SettlementError
        from app.settlement.near_intents import balance

        settlement = self.swarm.settlement
        try:
            self.wallet.live_balance = await asyncio.to_thread(
                balance, self.account, settlement["token"], contract=settlement["intents_contract"]
            )
        except (SettlementError, httpx.HTTPError, ValueError) as exc:  # RPC hiccups are not fatal
            logger.warning("%s could not read its balance: %s", self.id, exc)

    def feed_changed(self) -> bool:
        changed = self.swarm.feed.version != self._version
        self._version = self.swarm.feed.version
        return changed

    async def run(self):
        try:
            await self.read_balance()
            await self.loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("%s stopped after an error", self.id)
            self.log(f"{self.id} stopped after an error: {exc}.")
        if self.state != "broke":
            self.state = "done"

    async def loop(self):
        raise NotImplementedError


class Hunter(Agent):
    """Searches the site itself, claims what it can prove, and buys tips when they pay."""

    role = "hunter"

    def __init__(self, swarm, agent_id, identity, strategy, rng, start=0, pages=60):
        super().__init__(swarm, agent_id, identity, strategy, rng, start)
        spec = STRATEGIES[strategy]
        self.finders, self.buys_tips = spec.finders, spec.buys_tips
        self.margin = rng.uniform(*spec.margin)
        self.crawl = Crawl(swarm.site, swarm.site_url, max(1, round(pages * spec.page_share)), rng)
        self.queue: list[tuple[Finding, Tip | None]] = []
        self.known: set[str] = set()
        self.pending: dict[str, tuple[Finding | None, Tip | None]] = {}
        self.closed: set[str] = set()
        self.record = Record()
        self.facts: dict[str, list] = {}
        self.claims_made = self.proven = 0
        self.bond_failures = 0

    def counts(self):
        return self.claims_made, self.proven

    async def loop(self):
        while not self.swarm.stop.is_set():
            await self.sync()
            if self.finished():
                return
            if not await self.act():
                self.state = "waiting"
                await self.swarm.feed.wait(2.0)

    async def act(self) -> bool:
        if len(self.pending) < MAX_UNRESOLVED and self.wallet.can_afford(self.swarm.bond):
            item = self.next_finding()
            if item:
                await self.claim(*item)
                return True
            offers = self.swarm.board.offers_for(self.id) if self.buys_tips else []
            if offers:
                await self.negotiate(self.rng.choice(offers))
                return True
        if self.can_search():
            await self.search()
            return True
        return False

    # -- deciding when to stop

    def finished(self) -> bool:
        if is_broke(self.wallet, self.swarm.bond, len(self.pending)):
            self.state = "broke"
            self.log(
                f"{self.id} is broke: it has {usdc(self.wallet.available)} "
                f"and a bond costs {usdc(self.swarm.bond)}."
            )
            return True
        if self.bond_failures >= MAX_BOND_FAILURES and not self.pending:
            self.state = "done"
            self.log(f"{self.id} gave up: Arsune could not collect its bond.")
            return True
        if not self.pending and not self.has_more():
            self.state = "done"
            self.log(f"{self.id} is done: {self.claims_made} claims, {self.proven} proven.")
            return True
        return False

    def has_more(self) -> bool:
        may_buy = self.buys_tips and (
            self.swarm.board.offers_for(self.id) or self.swarm.scouts_searching()
        )
        return bool(self.queue or self.can_search() or may_buy)

    def can_search(self) -> bool:
        return not self.crawl.done

    # -- searching

    async def search(self):
        self.state = "searching"
        page = await self.crawl.step()
        if page and page.ok:
            for finding in await inspect(self.swarm, self.crawl, self.facts, self.finders, page):
                if finding.key not in self.known:
                    self.known.add(finding.key)
                    self.queue.append((finding, None))
        await self.think(0.1, 0.4)

    def next_finding(self) -> tuple[Finding, Tip | None] | None:
        while self.queue:
            finding, tip = self.queue.pop(0)
            # A careful hunter does not pay a bond for a fault that the public page already
            # shows as claimed: the same fault gets the same verdict, so it can only be a
            # duplicate. Claims made in the same second still race.
            if tip or finding.key not in self.swarm.feed.claimed:
                return finding, tip
        return None

    # -- buying a tip

    async def negotiate(self, tip: Tip):
        self.state = "negotiating"
        label = LABELS[tip.claim_type]
        proven, resolved = self.record.proven(tip.claim_type), self.record.resolved(tip.claim_type)
        chance = success_chance(proven, resolved)
        limit = max_share_bps(chance, self.swarm.reward, self.swarm.bond, self.margin)
        record = f"its record for {label} claims is {proven} proven of {resolved}"
        reason = (
            f"{record}, so {percent(limit)} is its limit"
            if limit >= MIN_COUNTER_BPS
            else f"{record}, which leaves no room for a share"
        )
        await self.think(0.3, 1.0)
        scout = self.swarm.agent(tip.scout)
        if await self.swarm.board.negotiate(tip, self.id, limit, reason, scout):
            await self.claim(tip.finding, tip)

    # -- claiming

    async def claim(self, finding: Finding, tip: Tip | None = None):
        self.state = "claiming"
        swarm = self.swarm
        settlement = await swarm.api.settlement() if swarm.live else swarm.settlement
        claim_id = str(uuid4())
        body = {
            "claim_id": claim_id,
            "hunter_account": self.account,
            **finding.body(),
            "bond_intent": sign_payment(self.identity, settlement, swarm.bond, bond_memo(claim_id)),
        }
        if tip:
            body["scout"] = {
                "account": tip.scout_account,
                "share_bps": tip.final_share_bps,
                "tip_id": tip.id,
            }
        self.known.add(finding.key)
        self.wallet.reserved += swarm.bond
        try:
            data = await swarm.api.submit_claim(swarm.bounty_id, body)
        except ApiError as exc:
            await self.refused(claim_id, finding, tip, exc)
            return
        except httpx.HTTPError as exc:
            # If Arsune saved the claim anyway, sync() adopts it from the public page.
            self.log(f"{self.id} could not reach Arsune to claim a {finding.label}: {exc!r}.")
            return
        finally:
            self.wallet.reserved -= swarm.bond
        self.wallet.apply(data.get("ledger", []))
        self.pending[claim_id] = (finding, tip)
        self.claims_made += 1
        self.bond_failures = 0
        if tip:
            tip.claim_id = claim_id
        how = "guessed" if finding.guessed else "claimed"
        via = f", bought from {tip.scout} for {percent(tip.final_share_bps)}" if tip else ""
        self.log(
            f"{self.id} {how} a {finding.label} on {finding.where}{via}, "
            f"with a {usdc(swarm.bond)} bond."
        )
        await self.read_balance()
        await self.think(0.3, 1.2)

    async def refused(self, claim_id: str, finding: Finding, tip: Tip | None, exc: ApiError):
        if exc.status in {429, 409}:
            # Too many open claims, or the bounty cannot take more: keep it and wait.
            self.queue.insert(0, (finding, tip))
            self.state = "waiting"
            await self.swarm.feed.wait(2.0)
            return
        if exc.status == 504:
            # Arsune holds the claim for review; its outcome arrives through sync().
            self.pending[claim_id] = (finding, tip)
            self.claims_made += 1
            self.log(f"{self.id}'s claim has an unknown bond transfer result; Arsune holds it.")
            return
        if exc.status == 402:
            self.bond_failures += 1
            await self.read_balance()
        if tip:
            tip.note = f"{self.id} bought it but could not claim it: {exc.detail}"
        self.log(f"Arsune refused {self.id}'s {finding.label} claim: {exc.detail}")

    # -- reading outcomes from the public bounty page

    async def sync(self):
        if not self.feed_changed():
            return
        feed = self.swarm.feed
        new_rows = self.wallet.apply(feed.rows_for(self.account))
        for claim in feed.claims_by(self.account):
            claim_id = claim["id"]
            if claim_id in self.closed:
                continue
            result = claim_result(claim)
            if claim_id not in self.pending:
                if result != "open":  # an old claim from before this run
                    self.closed.add(claim_id)
                    continue
                self.pending[claim_id] = (None, None)
            if result != "open":
                self.settle(claim, result)
        if new_rows:
            await self.read_balance()

    def settle(self, claim: dict, result: str):
        _, tip = self.pending.pop(claim["id"])
        self.closed.add(claim["id"])
        self.record.add(claim["claim_type"], result)
        if tip:
            tip.outcome = result
        label = LABELS.get(claim["claim_type"], claim["claim_type"])
        where = " and ".join(path(u) for u in claim["urls"])
        reason = (claim.get("checker_reason") or "no reason given").rstrip(".")
        if result == "proven":
            self.proven += 1
            reward = sum(
                r["amount"]
                for r in self.swarm.feed.rows_for(self.account)
                if r.get("claim_id") == claim["id"] and r["kind"] == "reward_out"
            )
            text = f"{self.id} was paid {usdc(reward)} and its bond back: its {label} on {where} was proven"
            if tip:
                text += f"; {tip.scout} earns its {percent(tip.final_share_bps)} share"
        elif result == "rejected":
            text = (
                f"{self.id} lost its bond: the checker could not reproduce the {label} on {where} "
                f"({reason})"
            )
        elif result == "duplicate":
            text = f"{self.id} got its bond back: the {label} on {where} was already proven"
        elif result == "unverified":
            text = f"{self.id} got its bond back: the checker could not decide ({reason})"
        else:
            note = (claim.get("settlement_note") or "").rstrip(".")
            text = f"{self.id}'s {label} claim is held for operator review" + (
                f" ({note})" if note else ""
            )
        self.log(text + ".")
        self.learn(result)

    def learn(self, result: str):
        pass


class Careless(Hunter):
    """Looks at a few pages and guesses. Many guesses are false, and each one costs a bond."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.guesses = 0
        self.out_of_ideas = False

    def next_finding(self):
        if self.queue:
            return super().next_finding()
        if self.guesses >= CARELESS_GUESSES or not self.crawl.pages:
            return None
        for _ in range(5):
            found = guess(list(self.crawl.pages.values()), self.rng)
            if found and found.key not in self.known:
                self.guesses += 1
                return found, None
        self.out_of_ideas = self.crawl.done
        return None

    def has_more(self) -> bool:
        ideas = self.guesses < CARELESS_GUESSES and not self.out_of_ideas
        return bool(self.queue or self.can_search() or ideas)


class Copycat(Hunter):
    """Re-submits faults that other hunters proved. Those are duplicates, so it learns to stop."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.losses_in_row = 0

    def can_search(self) -> bool:
        return False

    def next_finding(self):
        if self.losses_in_row >= self.swarm.copy_loss_limit:
            return None
        for key, claim in self.swarm.feed.proven.items():
            if key not in self.known and claim["hunter_account"] != self.account:
                self.known.add(key)
                return copy_of(claim), None
        return None

    def has_more(self) -> bool:
        if self.losses_in_row >= self.swarm.copy_loss_limit:
            return False
        fresh = any(k not in self.known for k in self.swarm.feed.proven)
        return fresh or self.swarm.finders_active()

    def learn(self, result: str):
        self.losses_in_row = 0 if result == "proven" else self.losses_in_row + 1
        if self.losses_in_row == self.swarm.copy_loss_limit:
            self.log(
                f"{self.id} stops copying: its last {self.losses_in_row} copies earned nothing."
            )


class Scout(Agent):
    """A fast crawler that never claims. It sells what it finds on the tip board."""

    role = "scout"

    def __init__(self, swarm, agent_id, identity, specialty, rng, start=0, pages=60):
        super().__init__(swarm, agent_id, identity, f"{specialty}-scout", rng, start)
        self.finders = SCOUT_FINDERS[specialty]
        self.hasty = specialty == "hasty"
        self.crawl = Crawl(swarm.site, swarm.site_url, pages, rng)
        self.ask = rng.choice([2500, 3000, 3500, 4000])
        self.floor_part = rng.uniform(0.55, 0.8)
        self.floors: dict[str, int] = {}
        self.tips: list[Tip] = []
        self.relist: list[tuple[Finding, int]] = []
        self.relisted: set[str] = set()
        self.facts: dict[str, list] = {}

    def counts(self):
        claimed = sum(1 for t in self.tips if t.claim_id)
        return claimed, sum(1 for t in self.tips if t.outcome == "proven")

    async def loop(self):
        while not self.swarm.stop.is_set():
            await self.sync()
            if self.relist:
                self.post(*self.relist.pop(0))
            elif not self.crawl.done:
                await self.search()
            elif not self.still_selling():
                for tip in self.tips:
                    self.swarm.board.withdraw(tip, "withdrawn: no buyer is left")
                self.log(f"{self.id} is done: {len(self.tips)} tips offered.")
                return
            else:
                self.state = "waiting"
                await self.swarm.feed.wait(2.0)

    def still_selling(self) -> bool:
        """Open offers that a buyer may still take, or sold tips whose claims are not settled."""
        offered = any(t.status == "offered" for t in self.tips) and self.swarm.buyers_active()
        return offered or any(t.claim_id and not t.outcome for t in self.tips)

    async def search(self):
        self.state = "searching"
        page = await self.crawl.step()
        if page and page.ok:
            if self.hasty:
                found = [guess(list(self.crawl.pages.values()), self.rng)]
            else:
                found = await inspect(self.swarm, self.crawl, self.facts, self.finders, page)
            for finding in filter(None, found):
                if finding.key not in self.swarm.feed.claimed and not self.swarm.board.is_listed(
                    finding.key
                ):
                    self.post(finding, self.ask)
        await self.think(0.05, 0.2)

    def post(self, finding: Finding, ask: int):
        tip = self.swarm.board.post(self.id, self.account, finding, ask)
        self.floors[tip.id] = int(ask * self.floor_part) // STEP_BPS * STEP_BPS
        self.tips.append(tip)

    async def answer(self, tip: Tip, hunter: str, share: int) -> bool:
        """Accept or refuse a hunter's counter-offer."""
        previous, self.state = self.state, "negotiating"
        await self.think(0.2, 0.8)
        floor = self.floors[tip.id]
        accepted = scout_accepts(share, floor)
        if accepted:
            self.log(f"{self.id} accepted {percent(share)} from {hunter}.")
        else:
            self.log(
                f"{self.id} refused {percent(share)} from {hunter}: its floor is {percent(floor)}."
            )
            if tip.finding.key not in self.relisted:
                # Offer it once more, half way between the old ask and the floor.
                self.relisted.add(tip.finding.key)
                lower = (tip.ask_share_bps + floor) // 2 // STEP_BPS * STEP_BPS
                self.relist.append((tip.finding, lower))
        self.state = previous if previous != "negotiating" else "waiting"
        return accepted

    async def sync(self):
        if not self.feed_changed():
            return
        new_rows = self.wallet.apply(self.swarm.feed.rows_for(self.account))
        if new_rows:
            await self.read_balance()
        for row in new_rows:
            if row["kind"] == "scout_share":
                buyer = next(
                    (t.buyer for t in self.tips if t.claim_id == row.get("claim_id")), None
                )
                self.log(
                    f"{self.id} earned {usdc(row['amount'])} from a tip: "
                    f"{buyer or 'its buyer'}'s claim was proven."
                )
        for tip in self.tips:
            holder = self.swarm.feed.claimed.get(tip.finding.key)
            if tip.status == "offered" and holder:
                who = self.swarm.name(holder["hunter_account"])
                self.swarm.board.withdraw(tip, f"withdrawn: {who} claimed this fault first")
