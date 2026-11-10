"""Fund a bounty from any chain through NEAR Intents 1Click.

1Click quotes an EXACT_OUTPUT swap: the payer sends the quoted amount of a supported asset to
a deposit address on its own chain, and 1Click delivers the exact bounty-token amount to
Arsune's account inside intents.near (`recipientType: INTENTS`). Arsune moves no money here.
It stores the signed quote, follows `GET /v0/status`, and when 1Click reports SUCCESS it
credits the delivered amount to the bounty once, as a `fund` ledger row whose memo names the
deposit address.
"""

import hashlib
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from threading import Lock
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import select

from app.bounty import budget, settlement
from app.core.config import ENV_FILE, settings
from app.core.db import Session
from app.core.models import Bounty, FundingRequest, LedgerEntry, utcnow
from app.core.schemas.bounties import MAX_AMOUNT

logger = logging.getLogger(__name__)


class OneClickSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ARSUNE_ONECLICK_", env_file=ENV_FILE, extra="ignore", env_ignore_empty=True
    )

    url: str = "https://1click.chaindefuser.com"
    # Optional partner JWT. Without it, 1Click adds 0.25% to each quote.
    jwt: SecretStr = SecretStr("")
    deadline_minutes: int = Field(default=60, ge=10, le=1440)
    slippage_bps: int = Field(default=100, ge=1, le=1000)


oneclick = OneClickSettings()

TOKENS_SECONDS = 600
# A read refreshes one request at most this often; the coordinator polls at most every 15 s.
STATUS_SECONDS = 10
POLL_SECONDS = 15
# After the send-before time, an address is only watched for a late deposit.
SLOW_STATUS_SECONDS = 300
# After 1Click fails to answer, reads show the stored status for this long.
BACKOFF_SECONDS = 20
# A deposit sent just before the deadline can take a while to appear in the status.
EXPIRY_GRACE = timedelta(minutes=15)
QUOTE_TIMEOUT = 20
READ_TIMEOUT = 6

STATUSES = {
    "PENDING_DEPOSIT": "pending_deposit",
    "KNOWN_DEPOSIT_TX": "deposit_seen",
    "PROCESSING": "processing",
    "INCOMPLETE_DEPOSIT": "incomplete_deposit",
    "REFUNDED": "refunded",
    "FAILED": "failed",
}
FINAL = ("credited", "refunded", "failed", "expired", "needs_review")
DRY_REASON = (
    "Funding from another chain needs live settlement on NEAR Intents. This Arsune runs in "
    "dry mode, where no real money can arrive. Use simulated funding instead."
)
ESCROW_REASON = (
    "Funding from another chain needs live settlement on NEAR Intents. This Arsune runs in "
    "escrow mode, where funds go to the escrow contract. Use a signed deposit instead."
)
OFFLINE_REASON = (
    "The 1Click API cannot be reached from this Arsune server, so funding from another chain "
    "is unavailable right now."
)
DIGITS = re.compile(r"[0-9]{1,78}")


class OneClickError(Exception):
    """1Click did not give a usable answer: network, timeout, server error, or bad data."""


class OneClickRefused(OneClickError):
    """1Click answered and refused the request. The text is 1Click's own message."""


_tokens: dict = {"items": None, "at": 0.0}
_tokens_lock = Lock()
_backoff = {"until": 0.0}
_poll = {"at": 0.0}


def _message(response: httpx.Response) -> str:
    try:
        text = response.json().get("message")
    except (ValueError, AttributeError):
        text = None
    return str(text)[:300] if text else f"HTTP {response.status_code}"


def _call(method: str, path: str, *, json=None, params=None, timeout=READ_TIMEOUT):
    headers = {"accept": "application/json"}
    if jwt := oneclick.jwt.get_secret_value():
        headers["authorization"] = f"Bearer {jwt}"
    try:
        response = httpx.request(
            method,
            oneclick.url.rstrip("/") + path,
            json=json,
            params=params,
            headers=headers,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise OneClickError("The 1Click API cannot be reached.") from exc
    if response.status_code in (400, 404, 422):
        raise OneClickRefused(_message(response))
    if response.status_code >= 400:
        raise OneClickError(f"The 1Click API answered HTTP {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise OneClickError("The 1Click API returned a response that is not JSON.") from exc


def _token(item) -> dict | None:
    if not isinstance(item, dict):
        return None
    asset, symbol, chain = item.get("assetId"), item.get("symbol"), item.get("blockchain")
    decimals = item.get("decimals")
    if not all(isinstance(v, str) and 0 < len(v) <= 200 for v in (asset, symbol, chain)):
        return None
    if not isinstance(decimals, int | float) or not 0 <= decimals <= 36:
        return None
    price = item.get("price")
    return {
        "asset_id": asset,
        "symbol": symbol[:40],
        "blockchain": chain[:40],
        "decimals": int(decimals),
        "price": price if isinstance(price, int | float) else None,
        "contract_address": item.get("contractAddress"),
    }


def tokens() -> list[dict]:
    """The assets 1Click can swap from, cached for 10 minutes (a stale list beats none)."""
    with _tokens_lock:
        items, at = _tokens["items"], _tokens["at"]
    if items is not None and time.monotonic() - at < TOKENS_SECONDS:
        return items
    try:
        data = _call("GET", "/v0/tokens")
        if not isinstance(data, list):
            raise OneClickError("The 1Click token list has an unknown format.")
    except OneClickError:
        if items:
            return items
        raise
    fresh = sorted(
        (t for t in map(_token, data) if t), key=lambda t: (t["blockchain"], t["symbol"])
    )
    with _tokens_lock:
        _tokens["items"], _tokens["at"] = fresh, time.monotonic()
    return fresh


def find_token(asset_id: str) -> dict | None:
    return next((t for t in tokens() if t["asset_id"] == asset_id), None)


def unavailable_reason() -> str:
    return ESCROW_REASON if settings.settlement_mode == "escrow" else DRY_REASON


def availability() -> dict:
    """Whether the owner page can offer funding from another chain, and why not."""
    if settings.settlement_mode != "near_intents":
        return {"available": False, "reason": unavailable_reason(), "tokens": []}
    try:
        items = tokens()
    except OneClickError:
        return {"available": False, "reason": OFFLINE_REASON, "tokens": []}
    return {"available": True, "reason": None, "tokens": items}


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_time(text) -> datetime:
    try:
        moment = datetime.fromisoformat(str(text))
    except ValueError as exc:
        raise OneClickError("1Click returned a time that cannot be read.") from exc
    if moment.tzinfo is None:
        raise OneClickError("1Click returned a time without a time zone.")
    return moment


def format_units(value: str, decimals: int) -> str:
    whole, fraction = divmod(int(value), 10**decimals)
    digits = str(fraction).rjust(decimals, "0").rstrip("0") if decimals else ""
    return f"{whole}.{digits}" if digits else str(whole)


def quote_body(*, origin_asset: str, amount: int, refund_to: str, deadline: datetime) -> dict:
    return {
        "dry": False,
        "swapType": "EXACT_OUTPUT",
        "slippageTolerance": oneclick.slippage_bps,
        "originAsset": origin_asset,
        "depositType": "ORIGIN_CHAIN",
        "destinationAsset": settings.bounty_token,
        "amount": str(amount),
        "refundTo": refund_to,
        "refundType": "ORIGIN_CHAIN",
        "recipient": settlement.arsune_account(),
        "recipientType": "INTENTS",
        "deadline": iso(deadline),
    }


def parse_quote(data, body: dict) -> dict:
    """Check that 1Click quoted what Arsune asked for, and return the fields Arsune keeps."""
    quote = data.get("quote") if isinstance(data, dict) else None
    if not isinstance(quote, dict):
        raise OneClickError("1Click returned a quote without details.")
    echoed = data.get("quoteRequest") or {}
    for key in ("recipient", "recipientType", "destinationAsset", "amount", "swapType"):
        if key in echoed and echoed[key] != body[key]:
            raise OneClickError(f"1Click returned a quote with a different {key}.")
    address, memo = quote.get("depositAddress"), quote.get("depositMemo") or ""
    if not isinstance(address, str) or not 1 <= len(address) <= 160 or not address.isprintable():
        raise OneClickError("1Click returned no usable deposit address.")
    if not isinstance(memo, str) or len(memo) > 100 or not memo.isprintable():
        raise OneClickError("1Click returned a deposit memo that cannot be stored.")
    amounts = {key: quote.get(key) for key in ("amountIn", "minAmountIn")}
    if not all(isinstance(v, str) and DIGITS.fullmatch(v) for v in amounts.values()):
        raise OneClickError("1Click returned an input amount that cannot be read.")
    # The asked deadline starts refunds; the quote deadline closes the address (often later).
    asked = parse_time(body["deadline"])
    closes = parse_time(quote["deadline"]) if quote.get("deadline") else asked
    return {
        "deposit_address": address,
        "deposit_memo": memo,
        "amount_in": amounts["amountIn"],
        "amount_in_formatted": str(quote.get("amountInFormatted") or "")[:80],
        "min_amount_in": amounts["minAmountIn"],
        "deadline": min(asked, closes),
    }


def closes_at(row: FundingRequest) -> datetime:
    """When the deposit address closes; Arsune watches it until then for a late deposit."""
    try:
        closes = parse_time((row.quote.get("quote") or {})["deadline"])
    except (KeyError, OneClickError):
        closes = row.deadline
    return max(row.deadline, closes)


def create_request(bounty_id: str, *, token: dict, amount: int, refund_to: str, now=None):
    """Ask 1Click for a deposit address that delivers `amount` to Arsune, and store it."""
    now = now or utcnow()
    body = quote_body(
        origin_asset=token["asset_id"],
        amount=amount,
        refund_to=refund_to,
        deadline=now + timedelta(minutes=oneclick.deadline_minutes),
    )
    data = _call("POST", "/v0/quote", json=body, timeout=QUOTE_TIMEOUT)
    fields = parse_quote(data, body)
    if not fields["amount_in_formatted"]:
        fields["amount_in_formatted"] = format_units(fields["amount_in"], token["decimals"])
    with Session.begin() as session:
        row = FundingRequest(
            id=str(uuid4()),
            bounty_id=bounty_id,
            status="pending_deposit",
            amount=amount,
            origin_asset=token["asset_id"],
            origin_symbol=token["symbol"],
            origin_chain=token["blockchain"],
            origin_decimals=token["decimals"],
            refund_to=refund_to,
            quote=data,
            settlement_mode=settlement.settlement_mode(),
            created_at=now,
            updated_at=now,
            **fields,
        )
        session.add(row)
        session.flush()
        return request_view(row)


def fund_memo(row: FundingRequest) -> str:
    """The ledger memo for one deposit address; the unique (memo, kind) row credits it once."""
    ref = row.deposit_address + (f":{row.deposit_memo}" if row.deposit_memo else "")
    memo = f"arsune:fund:1click:{ref}"
    if len(memo) <= 200:
        return memo
    return "arsune:fund:1click:sha256:" + hashlib.sha256(ref.encode()).hexdigest()


def refresh(request_id: str, *, now=None, force=False) -> bool:
    """Follow one request's 1Click status. Return False only when 1Click could not answer."""
    now = now or utcnow()
    with Session() as session:
        row = session.get(FundingRequest, request_id)
        if row is None or row.status in FINAL:
            return True
        late = now > row.deadline + EXPIRY_GRACE
        interval = timedelta(seconds=SLOW_STATUS_SECONDS if late else STATUS_SECONDS)
        recent = row.checked_at and now - row.checked_at < interval
        if recent and not force:
            return True
        params = {"depositAddress": row.deposit_address}
        if row.deposit_memo:
            params["depositMemo"] = row.deposit_memo
    try:
        data = _call("GET", "/v0/status", params=params)
    except OneClickRefused as exc:
        # 1Click can take a moment to know a fresh address; nothing has changed.
        logger.info("1Click has no status for funding request %s: %s", request_id, exc)
        data = {}
    except OneClickError as exc:
        logger.warning("1Click status for funding request %s failed: %s", request_id, exc)
        return False
    apply_status(request_id, data if isinstance(data, dict) else {}, now)
    return True


def apply_status(request_id: str, data: dict, now: datetime) -> None:
    raw = data.get("status")
    with Session.begin() as session:
        row = session.scalar(
            select(FundingRequest).where(FundingRequest.id == request_id).with_for_update()
        )
        if row is None or row.status in FINAL:
            return
        row.checked_at = now
        before = row.status
        if isinstance(raw, str):
            row.oneclick_status = raw[:30]
            if raw == "SUCCESS":
                credit(session, row, data, now)
            elif raw in STATUSES:
                row.status = STATUSES[raw]
        if row.status == "pending_deposit" and now > closes_at(row) + EXPIRY_GRACE:
            row.status = "expired"
            row.note = (
                "The deposit address closed without a deposit. 1Click refunds a later deposit "
                "to the refund address."
            )
        if row.status != before:
            row.updated_at = now


def credit(session, row: FundingRequest, data: dict, now: datetime) -> None:
    """Add the amount 1Click delivered to the bounty budget, exactly once per deposit address."""
    echoed = (data.get("quoteResponse") or {}).get("quoteRequest") or {}
    asked = row.quote.get("quoteRequest") or {}
    for key in ("recipient", "destinationAsset"):
        if key in echoed and key in asked and echoed[key] != asked[key]:
            return hold(row, f"1Click reported success for a different {key}.")
    details = data.get("swapDetails") or {}
    delivered = details.get("amountOut")
    if not isinstance(delivered, str) or not DIGITS.fullmatch(delivered):
        return hold(row, "1Click reported success without a delivered amount.")
    amount = int(delivered)
    if not 0 < amount <= MAX_AMOUNT:
        return hold(row, f"1Click reported a delivered amount of {delivered}, out of range.")
    bounty = session.scalar(select(Bounty).where(Bounty.id == row.bounty_id).with_for_update())
    memo = fund_memo(row)
    entry = session.scalar(
        select(LedgerEntry).where(LedgerEntry.memo == memo, LedgerEntry.kind == "fund")
    )
    reopened = False
    if entry is None:
        hashes = [h for h in details.get("nearTxHashes") or [] if isinstance(h, str)]
        entry = LedgerEntry(
            id=str(uuid5(NAMESPACE_URL, memo)),
            bounty_id=bounty.id,
            kind="fund",
            amount=amount,
            account=bounty.owner_account,
            memo=memo,
            tx_id=hashes[-1][:200] if hashes else None,
            settlement_mode=row.settlement_mode,
            status="completed",
            created_at=now,
            updated_at=now,
        )
        session.add(entry)
        reopened = budget.add_funds(bounty, amount, now)
        session.flush()
    row.status, row.ledger_entry_id, row.delivered_amount = "credited", entry.id, entry.amount
    notes = []
    if entry.amount < row.amount:
        notes.append(f"1Click delivered {entry.amount} units, less than the {row.amount} asked.")
    if reopened:
        notes.append("The bounty had closed because its budget ran out. These funds opened it.")
    elif bounty.status != "open":
        notes.append(f"The bounty was {bounty.status} when the funds arrived.")
    row.note = " ".join(notes) or None


def hold(row: FundingRequest, reason: str) -> None:
    row.status = "needs_review"
    row.note = (
        f"{reason} Nothing was credited. Check Arsune's balance in intents.near "
        "before any manual credit."
    )
    logger.warning("Funding request %s needs review: %s", row.id, reason)


def refresh_open(bounty_id: str | None = None, *, now=None, limit=10) -> bool:
    """Refresh unfinished requests, newest first. Return False when 1Click is unreachable."""
    if time.monotonic() < _backoff["until"]:
        return False
    with Session() as session:
        query = select(FundingRequest.id).where(FundingRequest.status.not_in(FINAL))
        if bounty_id:
            query = query.where(FundingRequest.bounty_id == bounty_id)
        ids = session.scalars(query.order_by(FundingRequest.created_at.desc()).limit(limit)).all()
    for request_id in ids:
        if not refresh(request_id, now=now):
            _backoff["until"] = time.monotonic() + BACKOFF_SECONDS
            return False
    return True


def poll_funding(now=None) -> None:
    """Coordinator step: credit 1Click deposits even when no one has the owner page open."""
    if time.monotonic() - _poll["at"] < POLL_SECONDS:
        return
    _poll["at"] = time.monotonic()
    try:
        refresh_open(now=now, limit=50)
    except Exception:
        logger.exception("Polling 1Click funding requests failed")


def request_view(row: FundingRequest) -> dict:
    quote = row.quote.get("quote") or {}
    return {
        "id": row.id,
        "bounty_id": row.bounty_id,
        "status": row.status,
        "final": row.status in FINAL,
        "oneclick_status": row.oneclick_status,
        "amount": row.amount,
        "origin_asset": row.origin_asset,
        "origin_symbol": row.origin_symbol,
        "origin_chain": row.origin_chain,
        "origin_decimals": row.origin_decimals,
        "deposit_address": row.deposit_address,
        "deposit_memo": row.deposit_memo or None,
        "amount_in": row.amount_in,
        "amount_in_formatted": row.amount_in_formatted,
        "min_amount_in": row.min_amount_in,
        "min_amount_in_formatted": format_units(row.min_amount_in, row.origin_decimals),
        "refund_to": row.refund_to,
        "deadline": row.deadline,
        "address_closes_at": closes_at(row),
        "time_estimate": quote.get("timeEstimate"),
        "delivered_amount": row.delivered_amount,
        "ledger_entry_id": row.ledger_entry_id,
        "note": row.note,
        "settlement_mode": row.settlement_mode,
        "checked_at": row.checked_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }
