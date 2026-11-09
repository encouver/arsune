"""Owner-funded bug bounties, hunter claims, and the settlement ledger."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade():
    op.create_table(
        "bounties",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(300), nullable=False),
        sa.Column("owner_account", sa.String(64), nullable=False),
        sa.Column("proof_token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reward_amount", sa.BigInteger(), nullable=False),
        sa.Column("bond_amount", sa.BigInteger(), nullable=False),
        sa.Column("fee_bps", sa.Integer(), nullable=False),
        sa.Column("budget_amount", sa.BigInteger(), nullable=False),
        sa.Column("budget_target", sa.BigInteger(), nullable=False),
        *timestamps(),
    )
    op.create_index("ix_bounties_status", "bounties", ["status"])
    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bounty_id", sa.String(36), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True, unique=True),
        sa.Column("hunter_account", sa.String(64), nullable=False),
        sa.Column("claim_type", sa.String(30), nullable=False),
        sa.Column("urls", sa.JSON(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("reproduction_steps", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=True),
        sa.Column("checker_reason", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("settlement_note", sa.Text(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *timestamps(),
    )
    op.create_index("ix_claims_bounty_id", "claims", ["bounty_id"])
    op.create_index("ix_claims_hunter_account", "claims", ["hunter_account"])
    op.create_index("ix_claims_status", "claims", ["status"])
    op.create_index(
        "uq_claims_proven_fingerprint",
        "claims",
        ["bounty_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("outcome = 'proven'"),
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bounty_id", sa.String(36), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("claim_id", sa.String(36), sa.ForeignKey("claims.id"), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("account", sa.String(64), nullable=False),
        sa.Column("memo", sa.String(200), nullable=False),
        sa.Column("tx_id", sa.String(200), nullable=True),
        sa.Column("settlement_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("claim_id", "kind", name="uq_ledger_entries_claim_kind"),
        sa.UniqueConstraint("memo", "kind", name="uq_ledger_entries_memo_kind"),
    )
    op.create_index("ix_ledger_entries_bounty_id", "ledger_entries", ["bounty_id"])
    op.create_index("ix_ledger_entries_claim_id", "ledger_entries", ["claim_id"])


def downgrade():
    op.drop_table("ledger_entries")
    op.drop_table("claims")
    op.drop_table("bounties")
