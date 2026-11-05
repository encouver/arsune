"""Link repeat checks to immutable baseline reports."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("baseline_job_id", sa.String(36), nullable=True))
    op.add_column(
        "jobs",
        sa.Column("comparison_mapping", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("jobs", "comparison_mapping", server_default=None)
    op.create_foreign_key("fk_jobs_baseline_job_id", "jobs", "jobs", ["baseline_job_id"], ["id"])


def downgrade():
    op.drop_constraint("fk_jobs_baseline_job_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "comparison_mapping")
    op.drop_column("jobs", "baseline_job_id")
