"""Arsune's public HTTP API, called the way any independent client would call it."""

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"HTTP {status}: {detail}")
        self.status, self.detail = status, detail


def _detail(response: httpx.Response) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    return detail if isinstance(detail, str) else str(detail)[:300]


class ArsuneApi:
    def __init__(self, base_url: str, transport: httpx.AsyncBaseTransport | None = None):
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(60, connect=10),
            transport=transport,
            limits=httpx.Limits(max_connections=32),
        )

    async def _call(self, method: str, path: str, body: dict | None = None, ok=(200,)) -> dict:
        response = await self.http.request(method, path, json=body)
        if response.status_code not in ok:
            raise ApiError(response.status_code, _detail(response))
        return response.json()

    async def settlement(self) -> dict:
        return await self._call("GET", "/api/settlement")

    async def create_bounty(self, body: dict) -> dict:
        return await self._call("POST", "/api/bounties", body, ok=(201,))

    async def verify_bounty(self, bounty_id: str) -> dict:
        """202 while Arsune checks the site; the bounty opens when the check passes."""
        return await self._call("POST", f"/api/bounties/{bounty_id}/verify", ok=(200, 202))

    async def request_site_check(self, bounty_id: str) -> dict:
        """Can Arsune's checker load the site now? Arsune reuses a result for a few minutes."""
        return await self._call("POST", f"/api/bounties/{bounty_id}/site-check", ok=(200, 202))

    async def site_check(self, bounty_id: str) -> dict:
        return await self._call("GET", f"/api/bounties/{bounty_id}/site-check")

    async def fund_bounty(self, bounty_id: str, body: dict) -> dict:
        return await self._call("POST", f"/api/bounties/{bounty_id}/fund", body)

    async def submit_claim(self, bounty_id: str, body: dict) -> dict:
        return await self._call("POST", f"/api/bounties/{bounty_id}/claims", body, ok=(202,))

    async def bounty(self, bounty_id: str) -> dict:
        return await self._call("GET", f"/api/bounties/{bounty_id}")

    async def bond_quote(self, bounty_id: str, hunter: str) -> dict:
        """The bond this hunter must sign now: it depends on the hunter's claim record."""
        response = await self.http.get(f"/api/bounties/{bounty_id}/bond", params={"hunter": hunter})
        if response.status_code != 200:
            raise ApiError(response.status_code, _detail(response))
        return response.json()

    async def close(self):
        await self.http.aclose()


def resolved(claim: dict) -> bool:
    """Settled, or held for an operator: either way the claim gets no more automatic updates."""
    return bool(claim.get("settled_at")) or claim.get("status") == "needs_review"


def outcome(claim: dict) -> str:
    return claim.get("outcome") or claim.get("status")


def claim_result(claim: dict) -> str:
    """proven, unfunded, rejected, duplicate, unverified, needs_review, or open."""
    if claim.get("status") == "needs_review":
        return "needs_review"
    if claim.get("status") == "unfunded":
        return "unfunded" if resolved(claim) else "open"
    return outcome(claim) if resolved(claim) else "open"


class BountyFeed:
    """One shared reader of the public bounty page, so a hundred agents do not each poll it.

    It holds only what `GET /api/bounties/{id}` shows to anyone.
    """

    def __init__(self, api: ArsuneApi, bounty_id: str, interval: float = 1.0):
        self.api, self.bounty_id, self.interval = api, bounty_id, interval
        self.bounty: dict = {}
        self.version = 0
        self._claims_by: dict[str, list[dict]] = {}
        self._rows_for: dict[str, list[dict]] = {}
        self._hunters: dict[str, dict] = {}  # account -> the public row with tier and bond
        self.proven: dict[str, dict] = {}  # fingerprint -> the claim that proved it
        # fingerprint -> first claim that is proven or still being checked
        self.claimed: dict[str, dict] = {}
        self._changed = asyncio.Event()

    async def refresh(self):
        data = await self.api.bounty(self.bounty_id)
        claims_by, rows_for, proven, claimed = {}, {}, {}, {}
        for claim in data.get("claims", []):
            claims_by.setdefault(claim["hunter_account"], []).append(claim)
            if outcome(claim) in {"proven", "paid"} and claim.get("status") != "unfunded":
                proven[claim["fingerprint"]] = claim
            if claim_result(claim) in {"open", "proven"}:
                claimed.setdefault(claim["fingerprint"], claim)
        for row in data.get("ledger", []):
            rows_for.setdefault(row["account"], []).append(row)
        self.bounty, self._claims_by, self._rows_for = data, claims_by, rows_for
        self._hunters = {row["account"]: row for row in data.get("hunters") or []}
        self.proven, self.claimed = proven, claimed
        self.version += 1
        changed, self._changed = self._changed, asyncio.Event()
        changed.set()

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await self.refresh()
            except (httpx.HTTPError, ApiError) as exc:
                logger.warning("Bounty feed read failed: %s", exc)
            try:
                await asyncio.wait_for(stop.wait(), self.interval)
            except TimeoutError:
                pass

    async def wait(self, timeout: float):
        """Return after the next refresh, or after `timeout` seconds."""
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
        except TimeoutError:
            pass

    @property
    def status(self) -> str | None:
        return self.bounty.get("status")

    @property
    def claims(self) -> list[dict]:
        return self.bounty.get("claims", [])

    def claims_by(self, account: str) -> list[dict]:
        return self._claims_by.get(account, [])

    def rows_for(self, account: str) -> list[dict]:
        return self._rows_for.get(account, [])

    def hunter(self, account: str) -> dict:
        return self._hunters.get(account, {})
