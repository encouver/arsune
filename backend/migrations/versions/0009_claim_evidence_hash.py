"""Store each decided claim's public evidence bundle and its sha256."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("evidence_hash", sa.String(64), nullable=True))
    op.add_column("claims", sa.Column("evidence_bundle", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("claims", "evidence_bundle")
    op.drop_column("claims", "evidence_hash")
