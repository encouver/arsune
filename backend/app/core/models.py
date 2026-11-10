from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), default="website")
    incident_input: Mapped[dict] = mapped_column(JSON, default=dict)
    baseline_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    comparison_mapping: Mapped[dict] = mapped_column(JSON, default=dict)
    url: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(20))
    inject_demo: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    generation: Mapped[int] = mapped_column(default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    checkpoint: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[dict] = mapped_column(
        JSON, default=lambda: {"stage": "queued", "message": "Waiting for a worker"}
    )
    report: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    generation: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    role: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Record(Base):
    __tablename__ = "records"
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    data: Mapped[dict] = mapped_column(JSON)


class PendingAction(Base):
    __tablename__ = "pending_actions"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    publish_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class IncidentSource(Base):
    __tablename__ = "incident_sources"
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), primary_key=True)
    filename: Mapped[str] = mapped_column(String(30), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text)


class Bounty(Base):
    __tablename__ = "bounties"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(300))
    owner_account: Mapped[str] = mapped_column(String(64))
    proof_token: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending_proof", index=True)
    # Why a closed bounty closed (app.bounty.budget); None while it is open.
    closed_reason: Mapped[str | None] = mapped_column(String(20))
    # Amounts are integers in the token's smallest unit (USDC has 6 decimals).
    reward_amount: Mapped[int] = mapped_column(BigInteger)
    bond_amount: Mapped[int] = mapped_column(BigInteger)
    fee_bps: Mapped[int] = mapped_column(Integer)
    budget_amount: Mapped[int] = mapped_column(BigInteger, default=0)
    budget_target: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    __tablename__ = "claims"
    # The first proven, funded claim holds its final fingerprint for the bounty; later equal
    # claims are duplicates. An unfunded claim has no final fingerprint, so it holds nothing.
    __table_args__ = (
        Index(
            "uq_claims_proven_final_fingerprint",
            "bounty_id",
            "final_fingerprint",
            unique=True,
            postgresql_where=text("outcome = 'proven'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bounty_id: Mapped[str] = mapped_column(ForeignKey("bounties.id"), index=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), unique=True)
    hunter_account: Mapped[str] = mapped_column(String(64), index=True)
    claim_type: Mapped[str] = mapped_column(String(30))
    urls: Mapped[list] = mapped_column(JSON)
    details: Mapped[dict] = mapped_column(JSON)
    explanation: Mapped[str] = mapped_column(Text)
    reproduction_steps: Mapped[list] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    # The fingerprint on the final URLs that the checker reached; set only for a funded proven claim.
    final_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Optional tip seller: {"account", "share_bps", "tip_id"}; paid from the reward when proven.
    scout: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="received", index=True)
    outcome: Mapped[str | None] = mapped_column(String(20))
    checker_reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    settlement_note: Mapped[str | None] = mapped_column(Text)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    # One entry of each kind per claim, so a repeated settlement step cannot add a second payment.
    __table_args__ = (
        UniqueConstraint("claim_id", "kind", name="uq_ledger_entries_claim_kind"),
        UniqueConstraint("memo", "kind", name="uq_ledger_entries_memo_kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bounty_id: Mapped[str] = mapped_column(ForeignKey("bounties.id"), index=True)
    claim_id: Mapped[str | None] = mapped_column(ForeignKey("claims.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[int] = mapped_column(BigInteger)
    account: Mapped[str] = mapped_column(String(64))
    memo: Mapped[str] = mapped_column(String(200))
    tx_id: Mapped[str | None] = mapped_column(String(200))
    settlement_mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FundingRequest(Base):
    """A 1Click deposit address that turns a payment on another chain into bounty funds."""

    __tablename__ = "funding_requests"
    # 1Click gives each quote its own address; memo chains can share an address with a memo.
    __table_args__ = (
        UniqueConstraint("deposit_address", "deposit_memo", name="uq_funding_requests_deposit"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    bounty_id: Mapped[str] = mapped_column(ForeignKey("bounties.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    oneclick_status: Mapped[str | None] = mapped_column(String(30))
    # The bounty-token amount (smallest unit) that the quote delivers to Arsune.
    amount: Mapped[int] = mapped_column(BigInteger)
    origin_asset: Mapped[str] = mapped_column(String(200))
    origin_symbol: Mapped[str] = mapped_column(String(40))
    origin_chain: Mapped[str] = mapped_column(String(40))
    origin_decimals: Mapped[int] = mapped_column(Integer)
    deposit_address: Mapped[str] = mapped_column(String(160))
    deposit_memo: Mapped[str] = mapped_column(String(100), default="")
    # Origin-asset amounts can exceed 64 bits (24-decimal tokens), so they stay text.
    amount_in: Mapped[str] = mapped_column(String(80))
    amount_in_formatted: Mapped[str] = mapped_column(String(80))
    min_amount_in: Mapped[str] = mapped_column(String(80))
    refund_to: Mapped[str] = mapped_column(String(200))
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The whole signed quote response, kept to resolve disputes with 1Click.
    quote: Mapped[dict] = mapped_column(JSON)
    delivered_amount: Mapped[int | None] = mapped_column(BigInteger)
    ledger_entry_id: Mapped[str | None] = mapped_column(
        ForeignKey("ledger_entries.id"), unique=True
    )
    note: Mapped[str | None] = mapped_column(Text)
    settlement_mode: Mapped[str] = mapped_column(String(20))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
