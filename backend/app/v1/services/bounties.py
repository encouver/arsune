"""Owner-funded bounties: owner proof, funding, hunter claims, and read views."""

import re
import secrets
from uuid import uuid4

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError

from app.bounty import access, budget, reputation, settlement, site_check
from app.bounty.claims import (
    ACCOUNT,
    MAX_UNRESOLVED_CLAIMS,
    ClaimError,
    canonical_url,
    fee_for,
    fingerprint,
    scoped_claim,
)
from app.bounty.proof import ProofError, proof_matches, proof_url
from app.bounty.recovery import queue_check
from app.browser.policy import TargetPolicyError, origin
from app.core.config import settings
from app.core.db import Session
from app.core.models import Bounty, Claim, Job, LedgerEntry, utcnow
from app.core.schemas.bounties import CreateBounty, CreateClaim, FundBounty, FundIntent
from app.settlement.base import SettlementError
from app.settlement.near_tx import OutcomeUnknown
from app.v1.services.jobs import JOB_ID

OUTCOME_COUNTS = ("proven", "unfunded", "rejected", "duplicate", "unverified")
MAX_ESCROW_FEE_BPS = 2_000
# A withdrawn escrow bounty is final: no top-up opens it again.
WITHDRAWN = "withdrawn"
FUNDS_RULE = (
    "Only an open bounty, or one that closed because its budget ran out, can receive funds."
)
# An Ethereum address in any letter case. NEAR account IDs, and so intents.near, are lowercase.
ANY_CASE_EVM_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def find_bounty(session, bounty_id, lock=False):
    # Bounty IDs use the same canonical UUID form as jobs; other text never reaches the database.
    query = select(Bounty).where(Bounty.id == bounty_id)
    bounty = (
        session.scalar(query.with_for_update() if lock else query)
        if JOB_ID.fullmatch(bounty_id)
        else None
    )
    if not bounty:
        raise HTTPException(404, "Bounty not found")
    return bounty


def find_claim(session, bounty_id, claim_id):
    claim = session.get(Claim, claim_id) if JOB_ID.fullmatch(claim_id) else None
    if not claim or claim.bounty_id != bounty_id:
        raise HTTPException(404, "Claim not found")
    return claim


def require_account(value, role="account"):
    if not ACCOUNT.fullmatch(value):
        if ANY_CASE_EVM_ADDRESS.fullmatch(value):
            raise HTTPException(
                422,
                f"Use the lowercase 0x address for the {role}: {value.lower()}. "
                "NEAR account IDs have no capital letters, so a checksummed address is not one.",
            )
        raise HTTPException(422, f"Use a valid NEAR account ID for the {role}.")
    if value == settlement.arsune_account():
        raise HTTPException(422, f"The {role} cannot be Arsune's settlement account.")


def check_intent(signed, *, sender, amount, memo, msg=None, note=""):
    try:
        settlement.check_transfer(signed, sender=sender, amount=amount, memo=memo, msg=msg)
    except SettlementError as exc:
        raise HTTPException(422, f"The signed transfer is not valid: {exc}{note}") from None


def bond_rule(quote):
    return (
        f"The bond for {quote['hunter']} on this bounty must be exactly "
        f"{quote['required_bond']} token units: the {quote['tier']} tier pays "
        f"{quote['multiplier']}x the base bond of {quote['base_bond']}."
    )


def fee_amount(bounty):
    return fee_for(bounty.reward_amount, bounty.fee_bps)


def require_one_transfer(amount):
    """near_intents moves money in single transfers of at most the payment cap.

    The check runs before any ledger row, so a refused fund_id stays unused.
    """
    cap = settings.max_payment_amount
    if settlement.settlement_mode() == "near_intents" and amount > cap:
        raise HTTPException(
            422,
            f"One funding transfer can be at most {cap} token units "
            "(ARSUNE_MAX_PAYMENT_AMOUNT). Split the funding into smaller transfers.",
        )


def settlement_view():
    try:
        return settlement.settlement_info()
    except SettlementError:
        raise HTTPException(503, "The settlement network is unavailable.") from None


def bounty_summary(bounty, mode):
    return {
        "id": bounty.id,
        "url": bounty.url,
        "origin": bounty.origin,
        "owner_account": bounty.owner_account,
        "status": bounty.status,
        "closed_reason": bounty.closed_reason,
        "paused_reason": bounty.paused_reason,
        "reward_amount": bounty.reward_amount,
        "bond_amount": bounty.bond_amount,
        "fee_bps": bounty.fee_bps,
        "fee_amount": fee_amount(bounty),
        "budget_amount": bounty.budget_amount,
        "budget_target": bounty.budget_target,
        "settlement_mode": mode,
        # Test account A for security claims; the markers are never shown here.
        "test_account": access.public_view(bounty.access_test),
        "security_test_ready": access.ready(bounty.access_test),
        "created_at": bounty.created_at,
        "updated_at": bounty.updated_at,
    }


def ledger_view(entry):
    return {
        "id": entry.id,
        "claim_id": entry.claim_id,
        "kind": entry.kind,
        "amount": entry.amount,
        "account": entry.account,
        "memo": entry.memo,
        "tx_id": entry.tx_id,
        "explorer_url": settlement.explorer_url(entry.settlement_mode, entry.tx_id),
        "settlement_mode": entry.settlement_mode,
        "status": entry.status,
        "created_at": entry.created_at,
    }


def claim_summary(claim):
    return {
        "id": claim.id,
        "claim_id": claim.id,
        "bounty_id": claim.bounty_id,
        "hunter_account": claim.hunter_account,
        "scout": claim.scout,
        "claim_type": claim.claim_type,
        "urls": claim.urls,
        "details": claim.details,
        "fingerprint": claim.fingerprint,
        "status": claim.status,
        "outcome": claim.outcome,
        "checker_reason": claim.checker_reason,
        "evidence_hash": claim.evidence_hash,
        "evidence_url": f"/api/claims/{claim.id}/evidence" if claim.evidence_hash else None,
        "settlement_note": claim.settlement_note,
        "job_id": claim.job_id,
        "created_at": claim.created_at,
        "updated_at": claim.updated_at,
        "settled_at": claim.settled_at,
    }


def hunter_summary(claims, ledger):
    """One row for every hunter and scout account seen in the bounty."""
    people = {}

    def person(account):
        return people.setdefault(
            account,
            {"account": account, "claims": 0, "pending": 0}
            | dict.fromkeys(OUTCOME_COUNTS, 0)
            | {"earned": 0, "bonds_lost": 0, "scout_earned": 0},
        )

    for claim in claims:
        row = person(claim.hunter_account)
        row["claims"] += 1
        row[claim.status if claim.status == "unfunded" else claim.outcome or "pending"] += 1
        if claim.scout:
            person(claim.scout["account"])
    for entry in ledger:
        if entry.kind == "reward_out" and entry.tx_id:
            person(entry.account)["earned"] += entry.amount
        elif entry.kind == "scout_share" and entry.tx_id:
            person(entry.account)["scout_earned"] += entry.amount
        elif entry.kind == "bond_forfeit":
            person(entry.account)["bonds_lost"] += entry.amount
    return sorted(people.values(), key=lambda p: (-p["earned"] - p["scout_earned"], p["account"]))


def ledger_rows(session, bounty_id, claim_id=None):
    query = select(LedgerEntry).where(LedgerEntry.bounty_id == bounty_id)
    if claim_id:
        query = query.where(LedgerEntry.claim_id == claim_id)
    return session.scalars(query.order_by(LedgerEntry.created_at, LedgerEntry.id)).all()


def bounty_detail(session, bounty):
    claims = session.scalars(
        select(Claim).where(Claim.bounty_id == bounty.id).order_by(Claim.created_at, Claim.id)
    ).all()
    ledger = ledger_rows(session, bounty.id)
    hunters = hunter_summary(claims, ledger)
    quotes = reputation.bond_quotes(session, bounty, [row["account"] for row in hunters])
    for row in hunters:
        quote = quotes[row["account"]]
        row["reputation_score"] = quote["reputation"]["score"]
        row["tier"], row["required_bond"] = quote["tier"], quote["required_bond"]
    return {
        **bounty_summary(bounty, settlement.settlement_mode()),
        "proof_token": bounty.proof_token,
        "proof_url": proof_url(bounty.url),
        "site_check": site_check.view(site_check.current(session, bounty)),
        "claims": [claim_summary(c) for c in claims],
        "ledger": [ledger_view(e) for e in ledger],
        "hunters": hunters,
    }


def claim_detail(session, claim):
    job = session.get(Job, claim.job_id) if claim.job_id else None
    evidence = [
        {
            **item,
            "screenshot_url": f"/api/jobs/{claim.job_id}/evidence/{item['screenshot']}"
            if item.get("screenshot")
            else None,
        }
        for item in claim.evidence
    ]
    return {
        **claim_summary(claim),
        "explanation": claim.explanation,
        "reproduction_steps": claim.reproduction_steps,
        "evidence": evidence,
        "job_status": job.status if job else None,
        "ledger": [ledger_view(e) for e in ledger_rows(session, claim.bounty_id, claim.id)],
        "settlement_mode": settlement.settlement_mode(),
    }


def reusable_proof_token(session, site_origin, pairs, account=None):
    """The token of the owner's newest earlier bounty on this origin, else a fresh token.

    The token is public on the site, so reuse needs the owner key of that earlier bounty, or
    a signed-in session of its owner_account (`account`). A typed owner_account alone is not
    enough. A pair that does not qualify is ignored without any answer.
    """
    ids = [pair.bounty_id for pair in pairs]
    owned = Bounty.id.in_(ids)
    if account:
        owned = or_(owned, Bounty.owner_account == account)
    rows = session.scalars(
        select(Bounty)
        .where(owned, Bounty.origin == site_origin, Bounty.status != "pending_proof")
        .order_by(Bounty.created_at.desc())
    ).all()
    keys = {pair.bounty_id: pair.owner_key for pair in pairs}
    for old in rows:
        if (account and old.owner_account == account) or access.owner_key_matches(
            old.owner_key_hash, keys.get(old.id)
        ):
            return old.proof_token
    return secrets.token_urlsafe(24)


async def create_bounty(request: CreateBounty, account=None):
    """`account` is the signed-in owner, if any: the bounty must be theirs."""
    require_account(request.owner_account, "owner")
    if account and request.owner_account != account:
        raise HTTPException(
            403, f"You are signed in as {account}. Use that account as the owner, or sign out."
        )
    try:
        url = await canonical_url(request.url)
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    if request.budget < request.reward_amount + fee_for(request.reward_amount, request.fee_bps):
        raise HTTPException(422, "The budget must cover at least one reward and its fee.")
    if (
        settings.settlement_mode == "near_intents"
        and request.reward_amount + request.bond_amount > settings.max_payment_amount
    ):
        raise HTTPException(422, "The reward and bond exceed the largest permitted payment.")
    if settings.settlement_mode == "escrow" and request.fee_bps > MAX_ESCROW_FEE_BPS:
        raise HTTPException(422, f"The escrow permits a fee of at most {MAX_ESCROW_FEE_BPS} bps.")
    owner_key, owner_key_hash = access.new_owner_key()
    with Session.begin() as session:
        proof_token = reusable_proof_token(session, origin(url), request.reuse_proof, account)
        bounty = Bounty(
            id=str(uuid4()),
            url=url,
            origin=origin(url),
            access_test=access.initial(url if origin(url) == origin(settings.demo_url) else None),
            owner_key_hash=owner_key_hash,
            owner_account=request.owner_account,
            proof_token=proof_token,
            status="pending_proof",
            reward_amount=request.reward_amount,
            bond_amount=request.bond_amount,
            fee_bps=request.fee_bps,
            budget_amount=0,
            budget_target=request.budget,
        )
        session.add(bounty)
        session.flush()
        # Shown only here: the owner needs it for owner-only actions such as the security test.
        return {**bounty_detail(session, bounty), "owner_key": owner_key}


def owned_bounty(session, bounty_id, owner_key, account=None, lock=False):
    """The bounty, for its owner key or a signed-in session of its owner_account."""
    bounty = find_bounty(session, bounty_id, lock=lock)
    signed_in = account is not None and account == bounty.owner_account
    if not signed_in and not access.owner_key_matches(bounty.owner_key_hash, owner_key):
        raise HTTPException(
            403,
            "Only the bounty owner can do this. Send the owner key, "
            "or sign in with the owner's NEAR account.",
        )
    return bounty


def get_access_test(bounty_id, owner_key, account=None):
    with Session() as session:
        bounty = owned_bounty(session, bounty_id, owner_key, account)
        return access.owner_view(bounty.access_test)


async def set_access_test(bounty_id, owner_key, request, account=None):
    try:
        with Session() as session:
            site_url = owned_bounty(session, bounty_id, owner_key, account).url
        test_account = await access.checked_account(
            site_url, request.login_url, request.email, request.password
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    with Session.begin() as session:
        bounty = owned_bounty(session, bounty_id, owner_key, account, lock=True)
        if bounty.status == "closed":
            raise HTTPException(409, not_open(bounty))
        current = bounty.access_test or {"markers": access.new_markers()}
        current.pop("login", None)
        bounty.access_test = {**current, "account": test_account}
        bounty.updated_at = utcnow()
        return access.owner_view(bounty.access_test)


def list_bounties(limit=50):
    mode = settlement.settlement_mode()
    with Session() as session:
        rows = session.scalars(
            select(Bounty).order_by(Bounty.created_at.desc(), Bounty.id.desc()).limit(limit)
        ).all()
        return {"items": [bounty_summary(b, mode) for b in rows], "settlement_mode": mode}


def get_bounty(bounty_id):
    with Session() as session:
        return bounty_detail(session, find_bounty(session, bounty_id))


def get_claim(bounty_id, claim_id):
    with Session() as session:
        find_bounty(session, bounty_id)
        return claim_detail(session, find_claim(session, bounty_id, claim_id))


def any_claim(session, claim_id):
    claim = session.get(Claim, claim_id) if JOB_ID.fullmatch(claim_id) else None
    if not claim:
        raise HTTPException(404, "Claim not found")
    return claim


def get_claim_by_id(claim_id):
    with Session() as session:
        return claim_detail(session, any_claim(session, claim_id))


def claim_evidence(claim_id):
    """The stored bundle bytes, so that their sha256 is the published evidence_hash."""
    with Session() as session:
        claim = any_claim(session, claim_id)
        if not claim.evidence_bundle:
            raise HTTPException(404, "The claim has no evidence bundle yet.")
        return claim.evidence_bundle.encode(), claim.evidence_hash


def bond_quote(session, bounty, hunter):
    return reputation.bond_quotes(session, bounty, [hunter])[hunter]


def get_bond(bounty_id, hunter):
    require_account(hunter, "hunter")
    with Session() as session:
        return bond_quote(session, find_bounty(session, bounty_id), hunter)


def get_hunter(account):
    require_account(account, "hunter")
    with Session() as session:
        counts = reputation.history(session, [account])[account]
    tier, bps = reputation.tier_for(counts)
    return {
        "account": account,
        "tier": tier,
        "multiplier": bps / 10_000,
        "reputation": reputation.reputation_view(counts),
    }


def not_open(bounty):
    """Why the bounty takes no claims now."""
    if bounty.status == "paused":
        return f"The bounty is paused: {bounty.paused_reason}"
    if bounty.status == "closed":
        return "The bounty is closed."
    return "The bounty is not open."


async def verify_bounty(bounty_id):
    """Read the owner's proof, then queue a site check. The worker opens the bounty on a pass.

    A paused bounty needs no new proof: the check alone decides if it opens again.
    """
    with Session() as session:
        bounty = find_bounty(session, bounty_id)
        if bounty.status == "open":
            return bounty_detail(session, bounty)
        if bounty.status not in ("pending_proof", "paused"):
            raise HTTPException(
                409, "Only a bounty that waits for proof, or a paused bounty, can be verified."
            )
        url, token, needs_proof = bounty.url, bounty.proof_token, bounty.status == "pending_proof"
    if needs_proof:
        try:
            await proof_matches(url, token)
        except ProofError as exc:
            raise HTTPException(409, str(exc)) from exc
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status in ("pending_proof", "paused"):
            # The owner asks again after a change on the site, so an old result is not reused.
            site_check.start(session, bounty, reuse=False)
        return bounty_detail(session, bounty)


def site_check_detail(session, bounty):
    return {
        "bounty_id": bounty.id,
        "bounty_status": bounty.status,
        "paused_reason": bounty.paused_reason,
        "site_check": site_check.view(site_check.current(session, bounty)),
    }


def request_site_check(bounty_id):
    """Start a check of an open bounty's site, or return a fresh one, before agents start."""
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status != "open":
            raise HTTPException(409, not_open(bounty))
        site_check.start(session, bounty, reuse=True)
        return site_check_detail(session, bounty)


def get_site_check(bounty_id):
    with Session() as session:
        return site_check_detail(session, find_bounty(session, bounty_id))


def withdraw_bounty(bounty_id):
    """Send what is left on the escrow to the owner, and unused storage NEAR back to Arsune.

    The escrow allows it after the owner's close or after the deadline, once no claim is open.
    """
    if settings.settlement_mode != "escrow":
        raise HTTPException(409, "Only an escrow bounty is withdrawn on chain.")
    with Session() as session:
        find_bounty(session, bounty_id)
    try:
        call = settlement.withdraw(bounty_id)
    except OutcomeUnknown:
        raise HTTPException(504, "The withdrawal result is unknown.") from None
    except SettlementError as exc:
        raise HTTPException(409, f"The escrow refused the withdrawal: {exc}") from None
    event = next((e for e in call.events if e["event"] == "withdraw"), {})
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        amount = int(event.get("amount", 0))
        session.add(
            settlement.escrow_row(bounty, "withdraw", amount, bounty.owner_account, call.tx_hash)
        )
        bounty.status, bounty.closed_reason = "closed", WITHDRAWN
        bounty.budget_amount, bounty.updated_at = 0, utcnow()
        return bounty_detail(session, bounty)


async def transfer_in(signed, *, sender, amount, memo, msg=None):
    """Collect a signed transfer. Return the transaction ID, or the unknown-result error."""
    return await run_in_threadpool(
        settlement.collect, signed, sender=sender, amount=amount, memo=memo, msg=msg
    )


def fund_intent(bounty_id, request: FundIntent):
    """The message, nonce and recipient a wallet signs (NEP-413) to fund with fund_id."""
    require_account(request.sender, "sender")
    require_one_transfer(request.amount)
    memo = f"arsune:fund:{request.fund_id}"
    with Session() as session:
        bounty = find_bounty(session, bounty_id)
        if session.get(LedgerEntry, request.fund_id):
            raise HTTPException(409, "This fund_id is already used.")
        if not budget.takes_funds(bounty):
            raise HTTPException(409, FUNDS_RULE)
    try:
        return settlement.transfer_payload(
            sender=request.sender,
            amount=request.amount,
            memo=memo,
            msg=settlement.fund_msg(bounty_id),
        )
    except SettlementError:
        raise HTTPException(503, "The settlement network is unavailable.") from None


async def fund_bounty(bounty_id, request: FundBounty):
    require_account(request.sender, "sender")
    require_one_transfer(request.amount)
    entry_id, memo = request.fund_id, f"arsune:fund:{request.fund_id}"
    with Session() as session:
        find_bounty(session, bounty_id)
        if session.get(LedgerEntry, entry_id):
            raise HTTPException(409, "This fund_id is already used.")
    msg = settlement.fund_msg(bounty_id)
    check_intent(
        request.signed_intent, sender=request.sender, amount=request.amount, memo=memo, msg=msg
    )
    try:
        registered = await run_in_threadpool(
            settlement.key_registered, request.signed_intent, sender=request.sender
        )
    except SettlementError:
        raise HTTPException(503, "The settlement network is unavailable.") from None
    if not registered:
        raise HTTPException(
            422,
            "This wallet key is not allowed in NEAR Intents yet. "
            "Click 'Allow this wallet key' first.",
        )
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if not budget.takes_funds(bounty):
            raise HTTPException(409, FUNDS_RULE)
        # The row exists before the transfer, so an interrupted request is visible as pending.
        session.add(
            LedgerEntry(
                id=entry_id,
                bounty_id=bounty.id,
                kind="fund",
                amount=request.amount,
                account=request.sender,
                memo=memo,
                settlement_mode=settlement.settlement_mode(),
                status="pending",
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "This fund_id is already used.") from None
    try:
        tx_id = await transfer_in(
            request.signed_intent, sender=request.sender, amount=request.amount, memo=memo, msg=msg
        )
    except SettlementError as exc:
        unknown = isinstance(exc, OutcomeUnknown)
        with Session.begin() as session:
            session.get(LedgerEntry, entry_id).status = "unknown" if unknown else "failed"
        if unknown:
            raise HTTPException(504, "The funding transfer result is unknown.") from None
        raise HTTPException(402, "The funding transfer failed.") from None
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        entry = session.get(LedgerEntry, entry_id)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
        budget.add_funds(bounty, request.amount, entry.updated_at)
        return bounty_detail(session, bounty)


async def create_claim(bounty_id, request: CreateClaim):
    require_account(request.hunter_account, "hunter")
    if request.scout:
        require_account(request.scout.account, "scout")
    claim_id, memo = request.claim_id, f"arsune:bond:{request.claim_id}"
    with Session() as session:
        bounty = find_bounty(session, bounty_id)
        if session.get(Claim, claim_id):
            raise HTTPException(409, "This claim_id is already used.")
        if bounty.status in ("closed", "paused"):
            raise HTTPException(409, not_open(bounty))
        site_origin = bounty.origin
        quote = bond_quote(session, bounty, request.hunter_account)
    try:
        urls, details = await scoped_claim(
            site_origin, request.claim_type, request.urls, request.details
        )
    except (ClaimError, TargetPolicyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    bond = quote["required_bond"]
    scout = request.scout.model_dump() if request.scout else None
    msg = settlement.bond_msg(bounty_id, claim_id, scout)
    check_intent(
        request.bond_intent,
        sender=request.hunter_account,
        amount=bond,
        memo=memo,
        msg=msg,
        note=". " + bond_rule(quote),
    )
    entry_id = str(uuid4())
    with Session.begin() as session:
        bounty = find_bounty(session, bounty_id, lock=True)
        if bounty.status != "open":
            raise HTTPException(409, not_open(bounty))
        if not budget.pays_one_reward(bounty):
            # A decision that spends the budget closes the bounty, so this one is not funded yet.
            raise HTTPException(
                409,
                f"The bounty is not funded yet: its budget of {bounty.budget_amount} token "
                f"units cannot pay one reward and its fee "
                f"({bounty.reward_amount + fee_amount(bounty)} token units). "
                "The owner must add funds first.",
            )
        unresolved = session.scalar(
            select(func.count())
            .select_from(Claim)
            .where(
                Claim.bounty_id == bounty.id,
                Claim.hunter_account == request.hunter_account,
                Claim.settled_at.is_(None),
            )
        )
        if unresolved >= MAX_UNRESOLVED_CLAIMS:
            raise HTTPException(429, "This hunter has too many unresolved claims on this bounty.")
        # A claim decided after the signature check can change the bond; charge the current one.
        quote = bond_quote(session, bounty, request.hunter_account)
        if quote["required_bond"] != bond:
            raise HTTPException(422, "The hunter's reputation changed. " + bond_rule(quote))
        session.add(
            Claim(
                id=claim_id,
                bounty_id=bounty.id,
                hunter_account=request.hunter_account,
                scout=scout,
                claim_type=request.claim_type,
                urls=urls,
                details=details,
                explanation=request.explanation,
                reproduction_steps=request.reproduction_steps,
                fingerprint=fingerprint(request.claim_type, urls, details),
                status="received",
                evidence=[],
            )
        )
        try:
            session.flush()
        except IntegrityError:
            raise HTTPException(409, "This claim_id is already used.") from None
        session.add(
            LedgerEntry(
                id=entry_id,
                bounty_id=bounty.id,
                claim_id=claim_id,
                kind="bond_in",
                amount=bond,
                account=request.hunter_account,
                memo=memo,
                settlement_mode=settlement.settlement_mode(),
                status="pending",
            )
        )
        site_url = bounty.url
    try:
        tx_id = await transfer_in(
            request.bond_intent, sender=request.hunter_account, amount=bond, memo=memo, msg=msg
        )
    except OutcomeUnknown as exc:
        with Session.begin() as session:
            claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
            session.get(LedgerEntry, entry_id).status = "unknown"
            claim.status, claim.updated_at = "needs_review", utcnow()
            claim.settlement_note = f"The bond transfer result is unknown (tx {exc.tx_hash})."
        raise HTTPException(504, "The bond transfer result is unknown.") from None
    except SettlementError:
        # The transfer did not happen, so the claim and its pending row are withdrawn.
        with Session.begin() as session:
            session.execute(delete(LedgerEntry).where(LedgerEntry.id == entry_id))
            session.execute(delete(Claim).where(Claim.id == claim_id))
        raise HTTPException(402, "The bond transfer failed.") from None
    record_bond(claim_id, entry_id, tx_id)
    return start_claim(claim_id, site_url)


def record_bond(claim_id, entry_id, tx_id):
    # Saved alone first, so recovery can queue the check if the next step does not run.
    with Session.begin() as session:
        claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
        entry = session.get(LedgerEntry, entry_id, with_for_update=True)
        entry.tx_id, entry.status, entry.updated_at = tx_id, "completed", utcnow()
        if claim.status == "needs_review" and not claim.job_id and not claim.settled_at:
            # Intake recovery held this claim while the transfer was slow; the result is known now.
            claim.status, claim.settlement_note, claim.updated_at = "received", None, utcnow()


def start_claim(claim_id, site_url):
    with Session.begin() as session:
        claim = session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
        queue_check(session, claim, site_url)
        return claim_detail(session, claim)
