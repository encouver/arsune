"""Store 1Click deposit addresses that fund a bounty from another chain."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "funding_requests",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bounty_id", sa.String(36), sa.ForeignKey("bounties.id"), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("oneclick_status", sa.String(30), nullable=True),
        sa.Column("amount", sa.BigInteger(), nullable=False),
        sa.Column("origin_asset", sa.String(200), nullable=False),
        sa.Column("origin_symbol", sa.String(40), nullable=False),
        sa.Column("origin_chain", sa.String(40), nullable=False),
        sa.Column("origin_decimals", sa.Integer(), nullable=False),
        sa.Column("deposit_address", sa.String(160), nullable=False),
        sa.Column("deposit_memo", sa.String(100), nullable=False),
        sa.Column("amount_in", sa.String(80), nullable=False),
        sa.Column("amount_in_formatted", sa.String(80), nullable=False),
        sa.Column("min_amount_in", sa.String(80), nullable=False),
        sa.Column("refund_to", sa.String(200), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quote", sa.JSON(), nullable=False),
        sa.Column("delivered_amount", sa.BigInteger(), nullable=True),
        sa.Column(
            "ledger_entry_id",
            sa.String(36),
            sa.ForeignKey("ledger_entries.id"),
            nullable=True,
            unique=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("settlement_mode", sa.String(20), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("deposit_address", "deposit_memo", name="uq_funding_requests_deposit"),
    )
    op.create_index("ix_funding_requests_bounty_id", "funding_requests", ["bounty_id"])
    op.create_index("ix_funding_requests_status", "funding_requests", ["status"])


def downgrade():
    op.drop_index("ix_funding_requests_status", table_name="funding_requests")
    op.drop_index("ix_funding_requests_bounty_id", table_name="funding_requests")
    op.drop_table("funding_requests")
