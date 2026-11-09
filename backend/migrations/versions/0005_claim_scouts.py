"""Record the scout that sold a tip for a claim."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("scout", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("claims", "scout")
