"""Start a swarm against one bounty, stop it, and report what happened."""

import asyncio
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx

from swarm.agents import Careless, Copycat, Hunter, Scout, Swarm
from swarm.arsune import ArsuneApi, BountyFeed, resolved
from swarm.identity import Identity, fund_memo, load_accounts, sign_payment
from swarm.market import TipBoard
from swarm.site import BrowserPool, SiteReader, origin
from swarm.state import SwarmState, serve_state, summarize, summary_lines, usdc

DEFAULT_MIX = {
    "links": 20,
    "mobile": 20,
    "prices": 15,
    "generalist": 25,
    "careless": 10,
    "copycat": 10,
}
DEFAULT_SCOUT_MIX = {"links": 4, "mobile": 3, "prices": 2, "hasty": 1}
AGENT_CLASSES = {"careless": Careless, "copycat": Copycat}
LIVE_CAP = 1_000_000  # the approved live budget: 1 USDC


class SwarmError(Exception):
    pass


@dataclass
class Config:
    live: bool = False
    api_url: str = "http://127.0.0.1:8540"
    site_url: str = "http://127.0.0.1:8541/shop/"
    bounty_id: str | None = None
    setup: bool = False
    hunters: int = 100
    scouts: int = 10
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
        if mode != "near_intents":
            raise SwarmError(f"Live mode needs Arsune in near_intents mode; it reports {mode!r}.")
        if config.setup and (config.budget > LIVE_CAP or config.reward + config.bond > LIVE_CAP):
            raise SwarmError("Live budgets are capped at 1 USDC, and reward plus bond at 1 USDC.")


def identities(config: Config) -> tuple[Identity, list[Identity], list[Identity]]:
    if not config.live:
        return (
            Identity.generate("owner"),
            [Identity.generate("hunter") for _ in range(config.hunters)],
            [Identity.generate("scout") for _ in range(config.scouts)],
        )
    accounts = load_accounts(config.accounts)
    owners = [a for a in accounts if a.role == "owner"]
    if config.setup and not owners:
        raise SwarmError(f"{config.accounts} has no owner account for --setup.")
    return (
        owners[0] if owners else None,
        [a for a in accounts if a.role == "hunter"][: config.hunters],
        [a for a in accounts if a.role == "scout"][: config.scouts],
    )


async def setup_bounty(config, api, site, owner, settlement, log) -> str:
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
    response = await site.http.post(
        origin(config.site_url) + "/bounty-proof-control", json={"token": bounty["proof_token"]}
    )
    response.raise_for_status()
    await api.verify_bounty(bounty["id"])
    fund_id = str(uuid4())
    await api.fund_bounty(
        bounty["id"],
        {
            "fund_id": fund_id,
            "sender": owner.account_id,
            "amount": config.budget,
            "signed_intent": sign_payment(owner, settlement, config.budget, fund_memo(fund_id)),
        },
    )
    log(
        "owner",
        f"The owner opened a bounty on {config.site_url}: {usdc(config.reward)} per proven bug, "
        f"{usdc(config.bond)} bond, funded with {usdc(config.budget)}.",
    )
    return bounty["id"]


def build_agents(config: Config, swarm: Swarm, hunters, scouts, rng) -> list:
    strategies = assign(len(hunters), config.mix, rng)
    agents = []
    for index, (identity, strategy) in enumerate(zip(hunters, strategies, strict=True), 1):
        cls = AGENT_CLASSES.get(strategy, Hunter)
        start = 0 if config.live else config.hunter_wallet
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
    specialties = assign(len(scouts), config.scout_mix, rng)
    for index, (identity, specialty) in enumerate(zip(scouts, specialties, strict=True), 1):
        agents.append(
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
    return agents


async def wait_for_end(swarm: Swarm, tasks: list, duration: float) -> str:
    deadline = time.monotonic() + duration
    while True:
        if all(task.done() for task in tasks):
            return "every agent is broke or done"
        if swarm.feed.status not in (None, "open"):
            return f"the bounty is {swarm.feed.status}"
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
) -> dict:
    rng = random.Random(config.seed)
    state = SwarmState(
        uuid4().hex[:12], "live" if config.live else "simulation", None if config.quiet else print
    )
    api, site = ArsuneApi(config.api_url, api_transport), SiteReader(site_transport)
    browser = BrowserPool(config.browser_pages)
    server = None
    if config.state_port is not None:
        server = await serve_state(state, config.state_host, config.state_port)
        port = server.sockets[0].getsockname()[1]
        state.log("swarm", f"Swarm state at http://{config.state_host}:{port}/state")
    try:
        settlement = await api.settlement()
        check_mode(config, settlement)
        owner, hunters, scouts = identities(config)
        if config.setup:
            bounty_id = await setup_bounty(config, api, site, owner, settlement, state.log)
        elif config.bounty_id:
            bounty_id = config.bounty_id
        else:
            raise SwarmError("Give --bounty-id or --setup.")
        state.bounty_id = bounty_id
        feed = BountyFeed(api, bounty_id, config.poll)
        await feed.refresh()
        if feed.status != "open":
            raise SwarmError(f"Bounty {bounty_id} is {feed.status}, not open.")
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
        )
        swarm.agents = state.agents = build_agents(config, swarm, hunters, scouts, rng)
        state.board = board
        state.log(
            "swarm",
            f"{len(hunters)} hunters and {len(scouts)} scouts start on bounty {bounty_id} "
            f"({state.mode}, settlement {settlement['mode']}).",
        )
        started = time.monotonic()
        feed_task = asyncio.create_task(feed.run(swarm.stop))
        tasks = [asyncio.create_task(agent.run()) for agent in swarm.agents]
        reason = await wait_for_end(swarm, tasks, config.duration)
        state.log("swarm", f"The swarm stops: {reason}.")
        swarm.stop.set()
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
        await browser.close()
        await api.close()
        await site.close()
