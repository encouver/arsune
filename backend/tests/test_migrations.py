"""Check migration history in an isolated PostgreSQL schema."""

from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from app.core.db import engine
from app.core.models import Base
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


def test_empty_schema_upgrade_downgrade_upgrade():
    schema = f"arsune_migration_test_{uuid4().hex}"
    config = Config("alembic.ini")
    tables = {
        "jobs",
        "attempts",
        "events",
        "records",
        "pending_actions",
        "incident_sources",
        "bounties",
        "claims",
        "ledger_entries",
    }
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


def test_bounty_migration_enforces_first_proven_claim_and_downgrades():
    schema = f"arsune_bounty_test_{uuid4().hex}"
    config = Config("alembic.ini")
    bounty_tables = {"bounties", "claims", "ledger_entries"}
    insert_claim = text("""INSERT INTO claims (id, bounty_id, hunter_account, claim_type, urls,
        details, explanation, reproduction_steps, fingerprint, status, outcome, evidence,
        created_at, updated_at) VALUES (:id, 'b1', :hunter, 'broken_link', '[]', '{}', 'x',
        '[]', 'same-fault', :outcome, :outcome, '[]', now(), now())""")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "0003")
            assert not bounty_tables & set(inspect(connection).get_table_names(schema=schema))
            command.upgrade(config, "0004")
            assert bounty_tables <= set(inspect(connection).get_table_names(schema=schema))
            connection.execute(
                text("""INSERT INTO bounties (id, url, origin, owner_account, proof_token, status,
                    reward_amount, bond_amount, fee_bps, budget_amount, budget_target,
                    created_at, updated_at) VALUES ('b1', 'https://site.test/',
                    'https://site.test:443', 'owner.near', 'token', 'open', 1000000, 100000,
                    1000, 0, 5000000, now(), now())""")
            )
            connection.execute(insert_claim, {"id": "c1", "hunter": "a.near", "outcome": "proven"})
            connection.execute(
                insert_claim, {"id": "c2", "hunter": "b.near", "outcome": "duplicate"}
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(
                    insert_claim, {"id": "c3", "hunter": "c.near", "outcome": "proven"}
                )
            command.downgrade(config, "0003")
            assert not bounty_tables & set(inspect(connection).get_table_names(schema=schema))
            assert "jobs" in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
