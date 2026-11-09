"""What the swarm board reads (`GET /state`) and the end-of-run summary."""

import asyncio
import json
from collections import Counter, deque
from datetime import UTC, datetime
from decimal import Decimal

from swarm.arsune import claim_result

LOG_SIZE = 200


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
        }


async def serve_state(state: SwarmState, host: str, port: int) -> asyncio.Server:
    """A tiny read-only HTTP server: GET /state returns the board JSON."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            request = (await asyncio.wait_for(reader.readline(), 5)).decode("latin-1").split()
            while (await asyncio.wait_for(reader.readline(), 5)).strip():
                pass
            method, target = (request + ["", ""])[:2]
            if method == "GET" and target.split("?")[0] == "/state":
                status, body = "200 OK", json.dumps(state.view()).encode()
            else:
                status, body = "404 Not Found", b'{"detail":"Not found"}'
            writer.write(
                f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
                "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except (TimeoutError, ConnectionError, UnicodeError):
            pass
        finally:
            writer.close()

    return await asyncio.start_server(handle, host, port)


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
        "wall_seconds": round(wall_seconds, 1),
        "leaderboard": [
            {"id": a.id, "strategy": a.strategy, "earned": a.wallet.earned, **_counts(a)}
            for a in leaders[:10]
            if a.wallet.earned
        ],
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
    for rank, row in enumerate(s["leaderboard"], 1):
        lines.append(
            f"Leaderboard {rank}: {row['id']} ({row['strategy']}) earned {usdc(row['earned'])} "
            f"from {row['proven']} proven of {row['claims']}."
        )
    return lines
