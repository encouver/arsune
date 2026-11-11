"""Start a swarm against one bounty, stop it, and report what happened."""

import asyncio
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

from swarm.agents import Careless, Copycat, Hunter, Scout, Swarm
from swarm.ai import AiHunter, AiScout
from swarm.arsune import ArsuneApi, BountyFeed, resolved
from swarm.identity import Identity, escrow_fund_msg, fund_memo, load_accounts, sign_payment
from swarm.llm import AiBudget, ModelClient, ModelSettings, model_settings
from swarm.market import TipBoard
from swarm.site import BrowserPool, SiteReader, origin
from swarm.state import SwarmState, serve_state, summarize, summary_lines, usdc

# The specialists follow the demo shop's bugs: 20 links, 15 overflows, 15 prices, 10 images,
# 6 page errors. Generalists run all five checks.
DEFAULT_MIX = {
    "links": 17,
    "mobile": 13,
    "prices": 12,
    "images": 8,
    "errors": 5,
    "generalist": 25,
    "careless": 10,
    "copycat": 10,
}
DEFAULT_SCOUT_MIX = {"links": 3, "mobile": 2, "prices": 2, "images": 1, "errors": 1, "hasty": 1}
AGENT_CLASSES = {"careless": Careless, "copycat": Copycat}
LIVE_CAP = 1_000_000  # the approved live budget: 1 USDC
MAX_AI_BUDGET_USD = 5.0  # a typo guard for --ai-budget
PAUSED = "The bounty is paused: Arsune cannot check this site — agents stop to save tokens."


class SwarmError(Exception):
    pass


class SiteUncheckable(SwarmError):
    """Arsune's checker cannot load the bounty site, so no agent starts."""

    def __init__(self, check: dict):
        super().__init__(check.get("reason") or "Arsune cannot check this site.")
        self.check = check


@dataclass
class Config:
    live: bool = False
    api_url: str = "http://127.0.0.1:8540"
    site_url: str = "http://127.0.0.1:8541/shop/"
    bounty_id: str | None = None
    setup: bool = False
    hunters: int = 100
    scouts: int = 10
    ai_hunters: int = 0
    ai_scouts: int = 0
    ai_budget: float = 0.5  # USD for all model calls of the run
    ai_model: ModelSettings | None = None  # None: ARSUNE_SWARM_MODEL_* / ARSUNE_MODEL_*
    mix: dict = field(default_factory=lambda: dict(DEFAULT_MIX))
    scout_mix: dict = field(default_factory=lambda: dict(DEFAULT_SCOUT_MIX))
    reward: int = 1_000_000
    bond: int = 100_000
    budget: int = 20_000_000
    fee_bps: int = 1000
    hunter_wallet: int = 300_000
    duration: float = 300
    pace: float = 1.0
    pages: int = 60
    browser_pages: int = 6
    copy_loss_limit: int = 3
    poll: float = 1.0
    settle_timeout: float = 120
    site_check_timeout: float = 180  # Arsune's site check runs in its worker queue
    state_host: str = "127.0.0.1"
    state_port: int | None = 8590
    seed: int | None = None
    accounts: Path | None = None  # None: ARSUNE_SWARM_ACCOUNTS_FILE
    approve_mainnet: bool = False
    hold: bool = False
    quiet: bool = False


def assign(count: int, weights: dict[str, int], rng: random.Random) -> list[str]:
    """`count` names in proportion to `weights` (largest remainder), in a shuffled order."""
    total = sum(weights.values())
    if count <= 0 or total <= 0:
        return []
    exact = {name: count * weight / total for name, weight in weights.items()}
    names = {name: int(value) for name, value in exact.items()}
    by_remainder = sorted(exact, key=lambda n: exact[n] - names[n], reverse=True)
    for name in by_remainder[: count - sum(names.values())]:
        names[name] += 1
    result = [name for name, n in names.items() for _ in range(n)]
    rng.shuffle(result)
    return result


def agent_id(role: str, index: int, count: int) -> str:
    return f"{role}-{index:0{max(2, len(str(count)))}d}"


def check_mode(config: Config, settlement: dict):
    mode = settlement.get("mode")
    if not config.live and mode != "dry":
        raise SwarmError(
            f"Simulation needs Arsune in dry settlement mode; Arsune reports {mode!r}."
        )
    if config.live:
        if not config.approve_mainnet:
            raise SwarmError("Live mode moves real USDC. Add --i-approve-mainnet to start it.")
        if mode not in ("near_intents", "escrow"):
            raise SwarmError(
                f"Live mode needs Arsune in near_intents or escrow mode; it reports {mode!r}."
            )
        if config.setup and (config.budget > LIVE_CAP or config.reward + config.bond > LIVE_CAP):
            raise SwarmError("Live budgets are capped at 1 USDC, and reward plus bond at 1 USDC.")


def start_ai(config: Config, transport=None) -> ModelClient | None:
    """The model client for AI agents, or None without them. Refuses unpriced models."""
    if not config.ai_hunters and not config.ai_scouts:
        return None
    settings = config.ai_model or model_settings()
    problem = settings.problem()
    if problem:
        raise SwarmError(problem)
    if not 0 < config.ai_budget <= MAX_AI_BUDGET_USD:
        raise SwarmError(f"--ai-budget must be above 0 and at most {MAX_AI_BUDGET_USD} USD.")
    budget = AiBudget(
        config.ai_budget, settings.input_usd_per_million, settings.output_usd_per_million
    )
    return ModelClient(settings, budget, transport)


def identities(config: Config) -> tuple[Identity, list[Identity], list[Identity]]:
    """Owner, hunters (scripted first, then AI) and scouts (scripted first, then AI)."""
    hunters, scouts = config.hunters + config.ai_hunters, config.scouts + config.ai_scouts
    if not config.live:
        return (
            Identity.generate("owner"),
            [Identity.generate("hunter") for _ in range(hunters)],
            [Identity.generate("scout") for _ in range(scouts)],
        )
    accounts = load_accounts(config.accounts)
    owners = [a for a in accounts if a.role == "owner"]
    if config.setup and not owners:
        raise SwarmError(f"{config.accounts} has no owner account for --setup.")
    return (
        owners[0] if owners else None,
        [a for a in accounts if a.role == "hunter"][:hunters],
        [a for a in accounts if a.role == "scout"][:scouts],
    )


async def wait_for_site_check(api, bounty_id, detail, config) -> dict:
    """Read Arsune's site check until it has a result; return the check."""
    deadline = time.monotonic() + config.site_check_timeout
    check = detail.get("site_check") or {}
    while check.get("state") == "checking":
        if time.monotonic() >= deadline:
            raise SwarmError(
                f"Arsune did not finish its site check in {config.site_check_timeout:.0f} s. "
                "No agent starts."
            )
        await asyncio.sleep(config.poll)
        check = (await api.site_check(bounty_id)).get("site_check") or {}
    if check.get("state") != "passed":
        raise SiteUncheckable(check)
    return check


async def require_checkable(config, api, bounty_id, state) -> dict:
    """Ask Arsune for a fresh site check before any agent starts or any model is called."""
    check = await wait_for_site_check(
        api, bounty_id, await api.request_site_check(bounty_id), config
    )
    state.site_check = check
    state.log("swarm", f"Site check passed: {check['reason']}")
    return check


async def setup_bounty(config, api, site, owner, settlement, state) -> str:
    """Create, prove and fund a bounty on the controlled demo site."""
    bounty = await api.create_bounty(
        {
            "url": config.site_url,
            "owner_account": owner.account_id,
            "reward_amount": config.reward,
            "bond_amount": config.bond,
            "fee_bps": config.fee_bps,
            "budget": config.budget,
        }
    )
    state.bounty_id = bounty["id"]
    response = await site.http.post(
        origin(config.site_url) + "/bounty-proof-control", json={"token": bounty["proof_token"]}
    )
    response.raise_for_status()
    # Arsune opens the bounty only after its own browser loads the site completely.
    await wait_for_site_check(api, bounty["id"], await api.verify_bounty(bounty["id"]), config)
    fund_id = str(uuid4())
    await api.fund_bounty(
        bounty["id"],
        {
            "fund_id": fund_id,
            "sender": owner.account_id,
            "amount": config.budget,
            "signed_intent": sign_payment(
                owner,
                settlement,
                config.budget,
                fund_memo(fund_id),
                msg=escrow_fund_msg(settlement, bounty["id"]),
            ),
        },
    )
    state.log(
        "owner",
        f"The owner opened a bounty on {config.site_url}: {usdc(config.reward)} per proven bug, "
        f"{usdc(config.bond)} bond, funded with {usdc(config.budget)}.",
    )
    return bounty["id"]


async def ready_bounty(config, api, site, owner, settlement, state) -> BountyFeed:
    """The feed of an open bounty whose site Arsune can check now. No agent exists yet."""
    if config.setup:
        bounty_id = await setup_bounty(config, api, site, owner, settlement, state)
    elif config.bounty_id:
        bounty_id = config.bounty_id
    else:
        raise SwarmError("Give --bounty-id or --setup.")
    state.bounty_id = bounty_id
    feed = BountyFeed(api, bounty_id, config.poll)
    await feed.refresh()
    if feed.status != "open":
        why = f": {feed.bounty['paused_reason']}" if feed.bounty.get("paused_reason") else "."
        raise SwarmError(f"Bounty {bounty_id} is {feed.status}, not open{why}")
    await require_checkable(config, api, bounty_id, state)
    return feed


async def refuse_to_start(exc: SiteUncheckable, config, server, state):
    """Show why no agent starts; with --hold, keep /state served so the board shows it."""
    state.site_check = exc.check
    state.log("swarm", f"No agent starts: {exc}")
    state.finished = True
    if config.hold and server:
        state.log("swarm", "Holding /state open; press Ctrl-C to exit.")
        await asyncio.Event().wait()


def build_agents(config: Config, swarm: Swarm, hunters, scouts, rng) -> list:
    """Scripted hunters, AI hunters, scripted scouts, AI scouts, in that order."""
    hunters, ai_hunters = hunters[: config.hunters], hunters[config.hunters :]
    scouts, ai_scouts = scouts[: config.scouts], scouts[config.scouts :]
    start = 0 if config.live else config.hunter_wallet
    strategies = assign(len(hunters), config.mix, rng)
    agents = []
    for index, (identity, strategy) in enumerate(zip(hunters, strategies, strict=True), 1):
        cls = AGENT_CLASSES.get(strategy, Hunter)
        agents.append(
            cls(
                swarm,
                agent_id("hunter", index, len(hunters)),
                identity,
                strategy,
                random.Random(rng.random()),
                start,
                config.pages,
            )
        )
    found = []
    specialties = assign(len(scouts), config.scout_mix, rng)
    for index, (identity, specialty) in enumerate(zip(scouts, specialties, strict=True), 1):
        found.append(
            Scout(
                swarm,
                agent_id("scout", index, len(scouts)),
                identity,
                specialty,
                random.Random(rng.random()),
                0,
                config.pages,
            )
        )
    for index, identity in enumerate(ai_hunters, 1):
        name = agent_id("hunter-ai", index, len(ai_hunters))
        agents.append(
            AiHunter(swarm, name, identity, "ai", random.Random(rng.random()), start, config.pages)
        )
    for index, identity in enumerate(ai_scouts, 1):
        name = agent_id("scout-ai", index, len(ai_scouts))
        found.append(AiScout(swarm, name, identity, random.Random(rng.random()), 0, config.pages))
    return agents + found


async def wait_for_end(swarm: Swarm, tasks: list, duration: float) -> str:
    deadline = time.monotonic() + duration
    while True:
        if all(task.done() for task in tasks):
            return "every agent is broke or done"
        if swarm.feed.status not in (None, "open"):
            return PAUSED if swarm.feed.status == "paused" else f"the bounty is {swarm.feed.status}"
        if time.monotonic() >= deadline:
            return "the time limit was reached"
        await asyncio.wait(tasks, timeout=0.5, return_when=asyncio.FIRST_COMPLETED)


async def wait_for_settlement(feed: BountyFeed, accounts: set, timeout: float, log) -> int:
    """Let Arsune finish checking the claims already made; return how many stay open."""
    deadline = time.monotonic() + timeout
    announced = False
    while True:
        try:
            await feed.refresh()
        except httpx.HTTPError:
            pass
        waiting = sum(1 for a in accounts for c in feed.claims_by(a) if not resolved(c))
        if not waiting or time.monotonic() >= deadline:
            return waiting
        if not announced:
            log("swarm", f"Waiting for Arsune to settle {waiting} open claims.")
            announced = True
        await asyncio.sleep(1)


async def run(
    config: Config,
    api_transport: httpx.AsyncBaseTransport | None = None,
    site_transport: httpx.AsyncBaseTransport | None = None,
    model_transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    rng = random.Random(config.seed)
    state = SwarmState(
        uuid4().hex[:12], "live" if config.live else "simulation", None if config.quiet else print
    )
    api, site = ArsuneApi(config.api_url, api_transport), SiteReader(site_transport)
    browser = BrowserPool(config.browser_pages)
    server, ai = None, None
    if config.state_port is not None:
        server = await serve_state(state, config.state_host, config.state_port)
        port = server.sockets[0].getsockname()[1]
        state.log("swarm", f"Swarm state at http://{config.state_host}:{port}/state")
    try:
        ai = state.ai = start_ai(config, model_transport)
        settlement = await api.settlement()
        check_mode(config, settlement)
        owner, hunters, scouts = identities(config)
        try:
            feed = await ready_bounty(config, api, site, owner, settlement, state)
        except SiteUncheckable as exc:
            await refuse_to_start(exc, config, server, state)
            raise
        bounty_id = feed.bounty_id
        board = TipBoard(state.log)
        swarm = Swarm(
            api,
            feed,
            site,
            browser,
            board,
            state.log,
            settlement,
            feed.bounty,
            live=config.live,
            pace=config.pace,
            copy_loss_limit=config.copy_loss_limit,
            ai=ai,
        )
        swarm.agents = state.agents = build_agents(config, swarm, hunters, scouts, rng)
        state.board = board
        state.log(
            "swarm",
            f"{len(hunters)} hunters and {len(scouts)} scouts start on bounty {bounty_id} "
            f"({state.mode}, settlement {settlement['mode']}).",
        )
        if ai:
            state.log(
                "swarm",
                f"{config.ai_hunters} AI hunters and {config.ai_scouts} AI scouts think with "
                f"{ai.settings.name} ({ai.settings.provider}); the AI budget is {config.ai_budget:.2f} USD.",
            )
        started = time.monotonic()
        feed_task = asyncio.create_task(feed.run(swarm.stop))
        tasks = [asyncio.create_task(agent.run()) for agent in swarm.agents]
        reason = await wait_for_end(swarm, tasks, config.duration)
        if ai:
            ai.halt()  # before the agents see the stop, so no later model call is made
        swarm.stop.set()
        if reason == PAUSED:
            state.log("swarm", PAUSED)
            state.log("swarm", f"Arsune's reason: {feed.bounty.get('paused_reason')}")
        else:
            state.log("swarm", f"The swarm stops: {reason}.")
        _, late = await asyncio.wait(tasks, timeout=20)
        for task in late:
            task.cancel()
        await asyncio.gather(*tasks, feed_task, return_exceptions=True)
        accounts = {agent.account for agent in swarm.agents}
        await wait_for_settlement(feed, accounts, config.settle_timeout, state.log)
        for agent in swarm.agents:
            await agent.sync()
            if agent.state != "broke":
                agent.state = "done"
        board.close("withdrawn: the run ended before a sale")
        summary = summarize(state, feed.claims, time.monotonic() - started)
        for line in summary_lines(summary):
            state.log("swarm", line)
        state.finished = True
        if config.hold and server:
            state.log("swarm", "Holding /state open; press Ctrl-C to exit.")
            await asyncio.Event().wait()
        return summary
    finally:
        if server:
            server.close()
        if ai:
            await ai.close()
        await browser.close()
        await api.close()
        await site.close()
