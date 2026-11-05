"""Separate incident jobs and retain immutable original input files."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "jobs", sa.Column("kind", sa.String(20), nullable=False, server_default="website")
    )
    op.add_column(
        "jobs",
        sa.Column("incident_input", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.alter_column("jobs", "kind", server_default=None)
    op.alter_column("jobs", "incident_input", server_default=None)
    op.create_table(
        "incident_sources",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("filename", sa.String(30), primary_key=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
    )
    # Reject accidental application updates to accepted source content.
    op.execute("""CREATE FUNCTION reject_incident_source_update() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'Incident sources are immutable'; END;
        $$ LANGUAGE plpgsql""")
    op.execute("""CREATE TRIGGER immutable_incident_sources BEFORE UPDATE ON incident_sources
        FOR EACH ROW EXECUTE FUNCTION reject_incident_source_update()""")


def downgrade():
    op.drop_table("incident_sources")
    op.execute("DROP FUNCTION reject_incident_source_update()")
    op.drop_column("jobs", "incident_input")
    op.drop_column("jobs", "kind")
