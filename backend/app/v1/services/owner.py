"""Owner sign-in with a NEAR wallet: one NEP-413 signature gives a 7-day session.

The session lets the owner act on every bounty whose owner_account is their account, in any
browser. Owner keys keep working beside it.
"""

import base64
import binascii
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime
from functools import cache

import redis
from fastapi import HTTPException
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey
from sqlalchemy import select

from app.bounty import settlement
from app.bounty.claims import ACCOUNT
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty
from app.settlement.base import SettlementError
from app.settlement.near_intents import nep413_hash
from app.settlement.near_tx import NearRpc, access_key, decode_ed25519
from app.v1.services.bounties import bounty_summary

RECIPIENT = "arsune.augevi.com"
CHALLENGE_TTL = 300
SESSION_SECONDS = 7 * 24 * 3600
CHALLENGE_KEY = "arsune:owner-challenge:"
# Without ARSUNE_OWNER_SESSION_SECRET, sessions last only until the API restarts.
PROCESS_SECRET = secrets.token_bytes(32)


@cache
def _redis(url):
    return redis.Redis.from_url(url)


def _secret() -> bytes:
    configured = settings.owner_session_secret.get_secret_value()
    return configured.encode() if configured else PROCESS_SECRET


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body: str) -> str:
    return _b64(hmac.new(_secret(), body.encode(), hashlib.sha256).digest())


def new_session(account: str, now: float | None = None) -> tuple[str, int]:
    expires = int((now or time.time()) + SESSION_SECONDS)
    body = _b64(f"{account}|{expires}".encode())
    return f"{body}.{_mac(body)}", expires


def session_account(authorization: str | None) -> str | None:
    """The account of a valid `Bearer <session>` header, else None."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    body, _, mac = authorization[len("Bearer ") :].strip().partition(".")
    if not body or not hmac.compare_digest(mac, _mac(body)):
        return None
    try:
        account, expires = _unb64(body).decode().split("|")
        if int(expires) <= time.time():
            return None
    except (ValueError, binascii.Error):
        return None
    return account if ACCOUNT.fullmatch(account) else None


def required_account(authorization: str | None) -> str:
    account = session_account(authorization)
    if not account:
        raise HTTPException(401, "Sign in with your NEAR account first.")
    return account


def challenge():
    nonce = secrets.token_bytes(32)
    issued = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    message = f"Sign in to Arsune to see your bug hunts.\nTime: {issued}"
    nonce_text = base64.b64encode(nonce).decode()
    _redis(settings.redis_url).set(CHALLENGE_KEY + nonce_text, message, ex=CHALLENGE_TTL)
    return {"message": message, "nonce": nonce_text, "recipient": RECIPIENT}


def _check_full_access(account_id: str, public_key: str):
    try:
        key = access_key(NearRpc(settings.near_rpc_url), account_id, public_key)
    except SettlementError:
        raise HTTPException(503, "The NEAR network is unavailable. Try again.") from None
    if not key or key.get("permission") != "FullAccess":
        raise HTTPException(403, "This key is not a full-access key of the account.")


def login(request):
    """Check the wallet's NEP-413 signature of a live challenge, then open a session."""
    if not ACCOUNT.fullmatch(request.account_id):
        raise HTTPException(422, "Use a valid NEAR account ID.")
    # GETDEL: a challenge signs in once, even when two requests race.
    message = _redis(settings.redis_url).getdel(CHALLENGE_KEY + request.nonce)
    if message is None:
        raise HTTPException(401, "The sign-in request expired or was used. Sign in again.")
    try:
        nonce = base64.b64decode(request.nonce, validate=True)
        public_key = decode_ed25519(request.public_key, 32, "public_key")
        signature = decode_ed25519(request.signature, 64, "signature")
        digest = nep413_hash(message.decode(), nonce, RECIPIENT)
        VerifyKey(public_key).verify(digest, signature)
    except (SettlementError, BadSignatureError, binascii.Error, ValueError):
        raise HTTPException(401, "The signature does not match.") from None
    _check_full_access(request.account_id, request.public_key)
    token, expires = new_session(request.account_id)
    return {"account_id": request.account_id, "session": token, "expires_at": expires}


def owner_bounties(account: str, limit=100):
    mode = settlement.settlement_mode()
    with Session() as session:
        rows = session.scalars(
            select(Bounty)
            .where(Bounty.owner_account == account)
            .order_by(Bounty.created_at.desc(), Bounty.id.desc())
            .limit(limit)
        ).all()
        items = [bounty_summary(b, mode) for b in rows]
    return {"account_id": account, "items": items, "settlement_mode": mode}
