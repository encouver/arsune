"""Hold proven faults by the final URLs the checker reached, not by the submitted URLs."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claims", sa.Column("final_fingerprint", sa.String(64), nullable=True))
    # Earlier proven claims keep their old fingerprint as their final one.
    op.execute("UPDATE claims SET final_fingerprint = fingerprint WHERE outcome = 'proven'")
    op.drop_index("uq_claims_proven_fingerprint", table_name="claims")
    op.create_index(
        "uq_claims_proven_final_fingerprint",
        "claims",
        ["bounty_id", "final_fingerprint"],
        unique=True,
        postgresql_where=sa.text("outcome = 'proven'"),
    )


def downgrade():
    op.drop_index("uq_claims_proven_final_fingerprint", table_name="claims")
    op.create_index(
        "uq_claims_proven_fingerprint",
        "claims",
        ["bounty_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("outcome = 'proven'"),
    )
    op.drop_column("claims", "final_fingerprint")
