"""Check migration history in an isolated PostgreSQL schema."""

from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.core.db import engine
from app.core.models import Base
from sqlalchemy import inspect, text


def test_empty_schema_upgrade_downgrade_upgrade():
    schema = f"arsune_migration_test_{uuid4().hex}"
    config = Config("alembic.ini")
    tables = {"jobs", "attempts", "events", "records", "pending_actions", "incident_sources"}
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            assert tables <= set(inspect(connection).get_table_names(schema=schema))
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
            command.downgrade(config, "base")
            assert not tables & set(inspect(connection).get_table_names(schema=schema))
            command.upgrade(config, "head")
            assert tables <= set(inspect(connection).get_table_names(schema=schema))
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_incident_upgrade_preserves_website_job():
    schema = f"arsune_legacy_test_{uuid4().hex}"
    config = Config("alembic.ini")
    job_id = str(uuid4())
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "0002")
            connection.execute(
                text("""INSERT INTO jobs (id, url, mode, inject_demo, status, generation,
                    created_at, updated_at, deadline, checkpoint, progress, comparison_mapping)
                    VALUES (:id, 'https://legacy.test', 'scripted', false, 'completed', 1,
                    now(), now(), now(), json_build_object('saved', true), json_build_object('stage', 'completed'), '{}')"""),
                {"id": job_id},
            )
            command.upgrade(config, "head")
            row = (
                connection.execute(
                    text("SELECT kind, incident_input, checkpoint, url FROM jobs WHERE id=:id"),
                    {"id": job_id},
                )
                .mappings()
                .one()
            )
            assert row["kind"] == "website"
            assert row["incident_input"] == {}
            assert row["checkpoint"] == {"saved": True}
            assert row["url"] == "https://legacy.test"
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
