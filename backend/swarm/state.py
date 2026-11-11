"""What the swarm board reads (`GET /state`) and the end-of-run summary."""

import asyncio
import json
import logging
from collections import Counter, deque
from datetime import UTC, datetime
from decimal import Decimal
from http import HTTPStatus

from swarm.arsune import claim_result

logger = logging.getLogger(__name__)

LOG_SIZE = 200
NO_AI = {"calls": 0, "usd_spent": 0.0, "budget_usd": 0.0, "model": ""}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def usdc(amount: int) -> str:
    return f"{Decimal(amount).scaleb(-6).normalize():f} USDC"


class SwarmState:
    def __init__(self, run_id: str, mode: str, echo=None):
        self.run_id, self.mode, self.echo = run_id, mode, echo
        self.bounty_id: str | None = None
        self.started_at = now_iso()
        self.finished = False
        self.agents: list = []
        self.board = None
        self.ai = None  # the run's model client when AI agents run
        # Arsune's answer to "can the checker load this site?", asked before any agent starts.
        self.site_check: dict | None = None
        self.events: deque[dict] = deque(maxlen=LOG_SIZE)

    def log(self, agent: str, text: str):
        entry = {"at": now_iso(), "agent": agent, "text": text}
        self.events.append(entry)
        if self.echo:
            self.echo(f"{entry['at'][11:19]}  {text}")

    def view(self) -> dict:
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "bounty_id": self.bounty_id,
            "started_at": self.started_at,
            "finished": self.finished,
            "agents": [agent.view() for agent in self.agents],
            "tips": [tip.view() for tip in self.board.tips.values()] if self.board else [],
            "log": list(self.events),
            "ai": self.ai_view(),
            "site_check": self.site_check,
        }

    def ai_view(self) -> dict:
        agents = sum(1 for agent in self.agents if agent.uses_ai)
        return {"agents": agents, **(self.ai.usage() if self.ai else NO_AI)}


MAX_BODY = 4096
NOT_FOUND = {"detail": "Not found"}


class BadRequest(Exception):
    pass


async def read_body(reader: asyncio.StreamReader, headers: dict) -> bytes:
    """The request body, with Content-Length or chunked transfer encoding (a proxy may send
    either), at most MAX_BODY bytes."""
    if "chunked" in headers.get("transfer-encoding", "").lower():
        body = bytearray()
        while size := int((await reader.readline()).split(b";")[0], 16):
            if len(body) + size > MAX_BODY:
                raise BadRequest("The request body is too large.")
            body += (await reader.readexactly(size + 2))[:-2]
        while (await reader.readline()).strip():  # trailers
            pass
        return bytes(body)
    length = int(headers.get("content-length") or 0)
    if not 0 <= length <= MAX_BODY:
        raise BadRequest("The request body is too large.")
    return await reader.readexactly(length)


async def read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict, bytes]:
    """(method, path without the query, lower-case headers, body)."""
    line = (await reader.readline()).decode("latin-1").split()
    method, target = (line + ["", ""])[:2]
    headers = {}
    while header := (await reader.readline()).decode("latin-1").strip():
        name, _, value = header.partition(":")
        headers[name.strip().lower()] = value.strip()
    try:
        body = await read_body(reader, headers)
    except ValueError:
        raise BadRequest("The request body is not readable.") from None
    return method, target.split("?")[0], headers, body


async def serve_http(respond, host: str, port: int) -> asyncio.Server:
    """A tiny JSON server. `respond(method, path, headers, body)` returns (status, payload).
    GET answers allow any origin; other methods do not, so another site's page cannot read
    what a POST did."""

    async def answer(reader: asyncio.StreamReader) -> tuple[str, int, dict]:
        try:
            method, path, headers, body = await asyncio.wait_for(read_request(reader), 5)
        except BadRequest as exc:
            return "", 400, {"detail": str(exc)}
        try:
            status, payload = await respond(method, path, headers, body)
        except Exception:
            logger.exception("The swarm's HTTP server failed to answer a request")
            status, payload = 500, {"detail": "The swarm failed to answer."}
        return method, status, payload

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            method, status, payload = await answer(reader)
            data = json.dumps(payload).encode()
            cors = "Access-Control-Allow-Origin: *\r\n" if method == "GET" else ""
            writer.write(
                f"HTTP/1.1 {status} {HTTPStatus(status).phrase}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
                f"Cache-Control: no-store\r\n{cors}Connection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except (
            TimeoutError,
            ConnectionError,
            UnicodeError,
            ValueError,
            asyncio.IncompleteReadError,
        ):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)


async def serve_state(state: SwarmState, host: str, port: int) -> asyncio.Server:
    """A read-only server: GET /state returns the board JSON."""

    async def respond(method, path, headers, body):
        if method == "GET" and path == "/state":
            return 200, state.view()
        return 404, NOT_FOUND

    return await serve_http(respond, host, port)


def summarize(state: SwarmState, claims: list[dict], wall_seconds: float) -> dict:
    """Numbers for the claims that this swarm's agents made."""
    accounts = {agent.account for agent in state.agents}
    ours = [c for c in claims if c["hunter_account"] in accounts]
    counts = Counter(claim_result(c) for c in ours)
    hunters = [a for a in state.agents if a.role == "hunter"]
    scouts = [a for a in state.agents if a.role == "scout"]
    tips = list(state.board.tips.values()) if state.board else []
    tip_status = Counter(t.status for t in tips)
    leaders = sorted(
        state.agents, key=lambda a: (a.wallet.earned, a.view()["proven"]), reverse=True
    )
    return {
        "claims": len(ours),
        "proven": counts["proven"],
        "rejected": counts["rejected"],
        "duplicate": counts["duplicate"],
        "unverified": counts["unverified"],
        "unfunded": counts["unfunded"],
        "needs_review": counts["needs_review"],
        "open": counts["open"],
        "hunter_rewards": sum(a.wallet.earned for a in hunters),
        "scout_shares": sum(a.wallet.earned for a in scouts),
        "bonds_lost": sum(a.wallet.bonds_lost for a in hunters),
        "tips_offered": len(tips),
        "tips_sold": tip_status["sold"],
        "tips_countered": sum(1 for t in tips if t.countered_by),
        "tips_declined": tip_status["declined"],
        "broke": sum(1 for a in state.agents if a.state == "broke"),
        "agents": len(state.agents),
        "tiers": dict(Counter(a.tier for a in hunters if a.tier)),
        "wall_seconds": round(wall_seconds, 1),
        "leaderboard": [
            {"id": a.id, "strategy": a.strategy, "earned": a.wallet.earned, **_counts(a)}
            for a in leaders[:10]
            if a.wallet.earned
        ],
        "ai": _ai_summary(state, ours, tips),
    }


def _ai_summary(state: SwarmState, claims: list[dict], tips: list) -> dict:
    hunters = {a.account for a in state.agents if a.uses_ai and a.role == "hunter"}
    buyers = {a.id for a in state.agents if a.uses_ai and a.role == "hunter"}
    scouts = {a.id for a in state.agents if a.uses_ai and a.role == "scout"}
    counts = Counter(claim_result(c) for c in claims if c["hunter_account"] in hunters)
    return {
        **state.ai_view(),
        "claims": sum(counts.values()),
        "proven": counts["proven"],
        "rejected": counts["rejected"],
        "duplicate": counts["duplicate"],
        "tips_bought": sum(1 for t in tips if t.status == "sold" and t.buyer in buyers),
        "tips_offered": sum(1 for t in tips if t.scout in scouts),
        "tips_sold": sum(1 for t in tips if t.status == "sold" and t.scout in scouts),
        "earned": sum(a.wallet.earned for a in state.agents if a.uses_ai),
        "bonds_lost": sum(a.wallet.bonds_lost for a in state.agents if a.uses_ai),
    }


def _counts(agent) -> dict:
    view = agent.view()
    return {"proven": view["proven"], "claims": view["claims"]}


def summary_lines(summary: dict) -> list[str]:
    s = summary
    lines = [
        (
            f"Summary: {s['claims']} claims, {s['proven']} proven, {s['rejected']} rejected, "
            f"{s['duplicate']} duplicates, {s['unverified']} unverified, "
            f"{s['unfunded']} proven but unfunded, "
            f"{s['needs_review']} in review, {s['open']} still open."
        ),
        (
            f"Payouts: {usdc(s['hunter_rewards'])} to hunters and {usdc(s['scout_shares'])} "
            f"to scouts; {usdc(s['bonds_lost'])} of bonds lost."
        ),
        (
            f"Tips: {s['tips_offered']} offered, {s['tips_sold']} sold, "
            f"{s['tips_countered']} countered, {s['tips_declined']} declined. "
            f"{s['broke']} of {s['agents']} agents went broke. "
            f"Wall time {s['wall_seconds']} s."
        ),
    ]
    if s["tiers"]:
        tiers = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in sorted(s["tiers"].items()))
        lines.append(f"Hunter reputation tiers at the end: {tiers}.")
    ai = s["ai"]
    if ai["agents"]:
        lines.append(
            f"AI: {ai['agents']} agents on {ai['model']} made {ai['calls']} model calls for "
            f"{ai['usd_spent']:.4f} USD of a {ai['budget_usd']:.2f} USD budget. AI hunters: "
            f"{ai['claims']} claims, {ai['proven']} proven, {ai['rejected']} rejected, "
            f"{ai['duplicate']} duplicates, {ai['tips_bought']} tips bought. AI scouts: "
            f"{ai['tips_offered']} tips offered, {ai['tips_sold']} sold."
        )
    for rank, row in enumerate(s["leaderboard"], 1):
        lines.append(
            f"Leaderboard {rank}: {row['id']} ({row['strategy']}) earned {usdc(row['earned'])} "
            f"from {row['proven']} proven of {row['claims']}."
        )
    return lines
