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
    # The first proven claim holds its fingerprint for the bounty; later equal claims are duplicates.
    __table_args__ = (
        Index(
            "uq_claims_proven_fingerprint",
            "bounty_id",
            "fingerprint",
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
