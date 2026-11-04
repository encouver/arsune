"""Persistent jobs, claims, evidence records, and outbox."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("inject_demo", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_table(
        "attempts",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_attempts_job_id", "attempts", ["job_id"])
    op.create_table(
        "events",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("role", sa.String(40), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_events_job_id", "events", ["job_id"])
    op.create_table(
        "records",
        sa.Column("id", sa.String(200), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    op.create_index("ix_records_job_id", "records", ["job_id"])
    op.create_table(
        "pending_actions",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("publish_after", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_pending_actions_job_id", "pending_actions", ["job_id"])


def downgrade():
    for table in ("pending_actions", "records", "events", "attempts"):
        op.drop_index(f"ix_{table}_job_id", table_name=table)
        op.drop_table(table)
    op.drop_index("ix_jobs_status", table_name="jobs")
    op.drop_table("jobs")
