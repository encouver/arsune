"""Check that Arsune can load a bounty site before the bounty opens, and pause a bounty whose
claims Arsune's own limits leave unverified."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "bounties",
        sa.Column("site_check_job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=True),
    )
    op.create_index("ix_bounties_site_check_job_id", "bounties", ["site_check_job_id"])
    op.add_column("bounties", sa.Column("paused_reason", sa.Text(), nullable=True))
    op.add_column(
        "bounties",
        sa.Column("unchecked_claims", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    # Before this revision a bounty is never paused, and no worker runs site checks.
    op.execute("UPDATE bounties SET status = 'open' WHERE status = 'paused'")
    op.execute(
        "UPDATE jobs SET status = 'failed', lease_until = NULL "
        "WHERE kind = 'site_check' AND status IN ('queued', 'running')"
    )
    op.execute(
        "DELETE FROM pending_actions WHERE job_id IN (SELECT id FROM jobs WHERE kind = 'site_check')"
    )
    op.drop_column("bounties", "unchecked_claims")
    op.drop_column("bounties", "paused_reason")
    op.drop_index("ix_bounties_site_check_job_id", table_name="bounties")
    op.drop_column("bounties", "site_check_job_id")
