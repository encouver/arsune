"""`python -m swarm serve`: a race host, so the web UI can start a race.

The host runs one race at a time and keeps the last race's state. It serves the board
(`GET /state`) and a small control API on the same port. No request can choose the mode. A
race is simulated (dry settlement) unless the operator gave the host a swarm accounts file
(`--accounts`) and Arsune reports a real settlement mode: then the race runs live with those
named accounts. Like every agent, it reaches Arsune only through Arsune's public HTTP API.
See docs/SWARM.md, "Race host".
"""

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path
from uuid import UUID, uuid4

import httpx
from app.settlement.base import SettlementError
from pydantic import ValidationError

from swarm.arsune import ApiError, ArsuneApi
from swarm.identity import load_accounts
from swarm.llm import ModelSettings, model_settings
from swarm.runner import (
    DEFAULT_MIX,
    DEFAULT_SCOUT_MIX,
    LIVE_CAP,
    Config,
    SiteUncheckable,
    SwarmError,
    run,
)
from swarm.state import NOT_FOUND, SwarmState, serve_http

logger = logging.getLogger(__name__)

# The race that a UI request starts. The strategy mixes are the defaults, so careless and
# copycat hunters take part: false claims lose their bond, and copies are duplicates.
UI_RACE = {"hunters": 12, "scouts": 3, "pace": 0.5, "duration": 240, "pages": 30}
AI_AGENTS = {"ai_hunters": 1, "ai_scouts": 0}  # every UI race adds this when a model is set
UI_AI_BUDGET_USD = 0.10  # an AI race spends at most this (one AI hunter run costs about 0.011)
AI_RACES_PER_DAY = 50
LIVE_MODES = {"near_intents", "escrow"}  # Arsune moves real USDC in these settlement modes
# Real money: only honest agents, so no bond is lost on purpose.
LIVE_MIX = {k: v for k, v in DEFAULT_MIX.items() if k not in ("careless", "copycat")}
LIVE_SCOUT_MIX = {k: v for k, v in DEFAULT_SCOUT_MIX.items() if k != "hasty"}

AI_RESTING = "AI agents are resting until tomorrow."
BUSY = "A race is already running."
IDLE = "No race is running."
RACE_FIELDS = {"bounty_id"}
PATHS = {"/state", "/host", "/races", "/races/stop"}


class Refusal(Exception):
    """An HTTP error answer: a status and one plain sentence."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def available_model() -> ModelSettings | None:
    """The swarm's model settings when AI agents can start with them, else None. Never logs
    a value: a validation error can quote what was set."""
    try:
        settings = model_settings()
    except ValidationError:
        print("swarm serve: AI agents are off: the ARSUNE_SWARM_MODEL_* settings are not valid.")
        return None
    problem = settings.problem()
    if problem:
        print(f"swarm serve: AI agents are off. {problem}")
        return None
    return settings


def parse_race(headers: dict, body: bytes) -> str:
    """The bounty_id from a POST /races request, or a 400 Refusal."""
    # A JSON content type makes a browser ask first (CORS preflight), which this server does
    # not allow, so another site's page cannot start a race.
    if "application/json" not in headers.get("content-type", "").lower():
        raise Refusal(400, "Send the body as JSON with Content-Type: application/json.")
    try:
        request = json.loads(body)
    except ValueError:
        raise Refusal(400, "The body is not valid JSON.") from None
    if not isinstance(request, dict):
        raise Refusal(400, 'The body must be a JSON object: {"bounty_id": "<uuid>"}.')
    unknown = sorted(set(request) - RACE_FIELDS)
    if unknown:
        raise Refusal(400, f"A race takes only bounty_id, not {', '.join(unknown)}.")
    try:
        return str(UUID(request.get("bounty_id")))
    except (TypeError, ValueError, AttributeError):
        raise Refusal(400, "bounty_id must be the UUID of an Arsune bounty.") from None


def live_accounts(path: Path) -> tuple[int, int]:
    """The hunter and scout accounts in the swarm accounts file. Raises SwarmError."""
    try:
        roles = [a.role for a in load_accounts(path)]
    except (SettlementError, KeyError, TypeError) as exc:  # names the file, never a key
        raise SwarmError(f"Cannot read the swarm accounts file: {exc}") from None
    hunters, scouts = roles.count("hunter"), roles.count("scout")
    if not hunters + scouts:
        raise SwarmError(f"{path} has no hunter or scout account.")
    return hunters, scouts


def idle_state() -> SwarmState:
    state = SwarmState(None, "simulation")
    state.finished = True
    state.log("swarm", "The race host is ready. No race has run yet.")
    return state


def race_ended(state: SwarmState, task: asyncio.Task):
    """Mark the race finished, also when a stop cancelled it, maybe before its first step."""
    if task.cancelled():
        state.log(
            "swarm",
            "The race host shut down during the race."
            if state.agents
            else "The race was stopped before any agent started.",
        )
    state.finished = True


class RaceHost:
    def __init__(
        self,
        api_url: str,
        *,
        model: ModelSettings | None = None,
        ai_cap_usd: float = UI_AI_BUDGET_USD,
        ai_races_per_day: int = AI_RACES_PER_DAY,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
        race: dict | None = None,
        browser_pages: int = 6,
        quiet: bool = False,
        accounts: Path | None = None,
        api_transport: httpx.AsyncBaseTransport | None = None,
        site_transport: httpx.AsyncBaseTransport | None = None,
        model_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.api_url, self.model, self.quiet = api_url, model, quiet
        # With an accounts file, a race on an Arsune that moves real money runs live.
        self.accounts = accounts
        self.live_size = live_accounts(accounts) if accounts else None
        self.live = False  # Arsune's last known mode is real money (and accounts are set)
        self.ai_max_usd = min(ai_cap_usd, UI_AI_BUDGET_USD)
        self.ai_races_per_day, self.today = ai_races_per_day, today
        self.ai_day, self.ai_used = today(), 0
        self.race = dict(UI_RACE if race is None else race)
        self.browser_pages = browser_pages
        self.transports = (api_transport, site_transport, model_transport)
        self.api = ArsuneApi(api_url, api_transport)
        self.state = idle_state()
        self.task: asyncio.Task | None = None
        self.stop_event = asyncio.Event()
        self.starting = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self.task is not None and not self.task.done()

    def ai_races_left(self) -> int:
        """AI races left today (UTC). 0 without a model."""
        if self.model is None:
            return 0
        if self.today() != self.ai_day:
            self.ai_day, self.ai_used = self.today(), 0
        return max(self.ai_races_per_day - self.ai_used, 0)

    async def refresh_mode(self) -> bool:
        """Ask Arsune for its settlement mode; True when a race now runs live."""
        if self.live_size is None:
            return False
        settlement = await self.api.settlement()
        self.live = settlement.get("mode") in LIVE_MODES
        return self.live

    def agents(self, ai: bool) -> dict:
        """The scripted agents of a race. Live: one per account; the AI hunter takes the last
        hunter account."""
        if not self.live:
            return {"hunters": self.race["hunters"], "scouts": self.race["scouts"]}
        hunters, scouts = self.live_size
        return {"hunters": hunters - ai, "scouts": scouts}

    def ai_for_race(self) -> bool:
        if self.live and not self.live_size[0]:
            return False  # no hunter account for the AI hunter
        return self.ai_races_left() > 0

    def view(self) -> dict:
        ai = self.ai_for_race()
        return {
            "busy": self.busy,
            "bounty_id": self.state.bounty_id,
            "run_id": self.state.run_id,
            "ai_available": self.model is not None,
            "ai_active": ai,
            "ai_note": AI_RESTING if self.model and not self.ai_races_left() else None,
            "ai_model": self.model.name if self.model else None,
            "ai_max_usd": self.ai_max_usd,
            "agents": {**self.agents(ai), **AI_AGENTS},
        }

    def race_config(self, bounty_id: str, ai: bool) -> Config:
        live = {}
        if self.live:
            live = {
                "live": True,
                "approve_mainnet": True,
                "accounts": self.accounts,
                "mix": dict(LIVE_MIX),
                "scout_mix": dict(LIVE_SCOUT_MIX),
            }
        return Config(
            api_url=self.api_url,
            bounty_id=bounty_id,
            browser_pages=self.browser_pages,
            ai_model=self.model,
            ai_budget=self.ai_max_usd,
            **{**self.race, **self.agents(ai), **live},
            **(AI_AGENTS if ai else {}),
        )

    async def respond(self, method: str, path: str, headers: dict, body: bytes):
        try:
            if (method, path) == ("GET", "/state"):
                return 200, self.state.view()
            if (method, path) == ("GET", "/host"):
                return 200, self.view()
            if (method, path) == ("POST", "/races"):
                return await self.start(headers, body)
            if (method, path) == ("POST", "/races/stop"):
                return self.stop()
        except Refusal as exc:
            return exc.status, {"detail": exc.detail}
        if path in PATHS:
            return 405, {"detail": f"{method} is not allowed on {path}."}
        return 404, NOT_FOUND

    async def start(self, headers: dict, body: bytes) -> tuple[int, dict]:
        bounty_id = parse_race(headers, body)
        async with self.starting:  # a second request waits here, then sees the race
            if self.busy:
                raise Refusal(409, BUSY)
            bounty = await self.require_open(bounty_id)
            if await self.mode_for_race():
                limit = bounty.get("reward_amount", 0) + bounty.get("bond_amount", 0)
                if limit > LIVE_CAP:
                    raise Refusal(
                        409, "A real-money race needs a reward plus bond of at most 1 USDC."
                    )
            ai = self.ai_for_race()
            answer = self.launch(self.race_config(bounty_id, ai), live=self.live)
            self.ai_used += ai
            return 202, answer

    async def mode_for_race(self) -> bool:
        try:
            return await self.refresh_mode()
        except ApiError as exc:
            raise Refusal(502, f"Arsune answered HTTP {exc.status}: {exc.detail}") from None
        except httpx.HTTPError:
            raise Refusal(502, "Arsune's API did not answer.") from None

    async def require_open(self, bounty_id: str) -> dict:
        """Refuse a bounty that Arsune shows as not open, or without the funds for a reward."""
        try:
            bounty = await self.api.bounty(bounty_id)
        except ApiError as exc:
            if exc.status == 404:
                raise Refusal(404, f"Arsune has no bounty {bounty_id}.") from None
            raise Refusal(502, f"Arsune answered HTTP {exc.status}: {exc.detail}") from None
        except httpx.HTTPError:
            raise Refusal(502, "Arsune's API did not answer.") from None
        status = bounty.get("status")
        if status != "open":
            why = f": {bounty['paused_reason']}" if bounty.get("paused_reason") else "."
            raise Refusal(409, f"The bounty is {status}, not open{why}")
        one_reward = bounty.get("reward_amount", 0) + bounty.get("fee_amount", 0)
        if bounty.get("budget_amount", 0) < one_reward:
            raise Refusal(
                409, "The bounty has no funds left: its budget cannot pay one more reward."
            )
        return bounty

    def launch(self, config: Config, live: bool = False) -> dict:
        """Start a race now. It is a simulation (dry settlement) unless `live`, which only
        start() passes, after Arsune reported a real settlement mode and accounts are set."""
        live = live and self.live_size is not None
        config = replace(
            config,
            live=live,
            approve_mainnet=live,
            accounts=self.accounts if live else None,
            hold=False,
            state_port=None,
            quiet=self.quiet,
        )
        mode = "live" if live else "simulation"
        self.state = SwarmState(uuid4().hex[:12], mode, None if self.quiet else print)
        self.state.bounty_id = config.bounty_id  # known at once for a UI race
        self.stop_event = asyncio.Event()
        self.task = asyncio.create_task(self._race(config, self.state, self.stop_event))
        self.task.add_done_callback(partial(race_ended, self.state))
        return {"run_id": self.state.run_id, "bounty_id": config.bounty_id}

    async def _race(self, config: Config, state: SwarmState, stop: asyncio.Event):
        api, site, model = self.transports
        try:
            await run(config, api, site, model, state=state, stop=stop)
        except SiteUncheckable:
            pass  # run() logged why no agent starts
        except SwarmError as exc:
            state.log("swarm", f"The race did not start: {exc}")
        except Exception as exc:
            # For example Arsune down or an unexpected API answer. Swarm errors never hold keys.
            logger.exception("The race failed")
            state.log("swarm", f"The race failed: {str(exc)[:300] or type(exc).__name__}")

    def stop(self) -> tuple[int, dict]:
        if not self.busy:
            raise Refusal(409, IDLE)
        self.stop_event.set()
        if not self.state.agents:
            # run() builds and starts every agent without a pause, so while it has none, no
            # agent task exists: a cancel ends the setup or the site check and leaves nothing.
            self.task.cancel()
        return 202, {"run_id": self.state.run_id, "bounty_id": self.state.bounty_id}

    async def close(self):
        if self.busy:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.api.close()


async def serve(host: RaceHost, bind: str, port: int, demo: Config | None = None):
    """Serve until the process ends. `demo`: a race to start at once (simulate --setup)."""
    server = await serve_http(host.respond, bind, port)
    port = server.sockets[0].getsockname()[1]
    try:
        if await host.refresh_mode():
            print("swarm serve: Arsune moves real money: races run live.", flush=True)
    except (ApiError, httpx.HTTPError):
        pass  # every race asks again before it starts
    print(f"Race host at http://{bind}:{port}: GET /state, GET /host, POST /races.", flush=True)
    if demo:
        host.launch(demo)
    try:
        await asyncio.Event().wait()
    finally:
        server.close()
        await host.close()
