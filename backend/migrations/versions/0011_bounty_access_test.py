"""The owner's security test (test account A and secret markers) and the owner key."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("bounties", sa.Column("access_test", sa.JSON(), nullable=True))
    op.add_column("bounties", sa.Column("owner_key_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("bounties", "owner_key_hash")
    op.drop_column("bounties", "access_test")
