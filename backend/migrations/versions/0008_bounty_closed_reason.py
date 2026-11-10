"""Record why a bounty closed, so a top-up can open a bounty whose budget ran out."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bounties", sa.Column("closed_reason", sa.String(20), nullable=True))
    # Before this revision, a spent budget was the only reason for which a bounty closed.
    op.execute("UPDATE bounties SET closed_reason = 'budget_spent' WHERE status = 'closed'")


def downgrade():
    op.drop_column("bounties", "closed_reason")
