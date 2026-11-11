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

# The columns of 0010; its foreign key and index also differ until it runs.
SITE_CHECK_COLUMNS = {
    ("bounties", "site_check_job_id"),
    ("bounties", "paused_reason"),
    ("bounties", "unchecked_claims"),
}


def added_columns(connection):
    """(table, column) for each model column that the schema does not have yet."""
    later = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert {d[0] for d in later} <= {"add_column", "add_index", "add_fk"}
    return {(d[2], d[3].name) for d in later if d[0] == "add_column"}


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
            columns = {c["name"] for c in inspect(connection).get_columns("claims", schema=schema)}
            assert "scout" not in columns
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
            command.upgrade(config, "0005")
            columns = {c["name"] for c in inspect(connection).get_columns("claims", schema=schema)}
            assert "scout" in columns
            scouts = connection.execute(text("SELECT scout FROM claims ORDER BY id")).scalars()
            assert list(scouts) == [None, None]
            command.downgrade(config, "0003")
            assert not bounty_tables & set(inspect(connection).get_table_names(schema=schema))
            assert "jobs" in inspect(connection).get_table_names(schema=schema)
            command.upgrade(config, "head")
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_migration_0008_marks_closed_bounties_as_closed_by_a_spent_budget():
    schema = f"arsune_closed_reason_test_{uuid4().hex}"
    config = Config("alembic.ini")
    insert = text("""INSERT INTO bounties (id, url, origin, owner_account, proof_token, status,
        reward_amount, bond_amount, fee_bps, budget_amount, budget_target, created_at,
        updated_at) VALUES (:id, 'https://site.test/', 'https://site.test:443', 'owner.near',
        'token', :status, 1000000, 100000, 1000, 0, 5000000, now(), now())""")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "0007")
            for bounty_id, status in [("b1", "closed"), ("b2", "open"), ("b3", "pending_proof")]:
                connection.execute(insert, {"id": bounty_id, "status": status})
            command.upgrade(config, "0008")
            reasons = connection.execute(text("SELECT id, closed_reason FROM bounties ORDER BY id"))
            # Before 0008 a spent budget was the only reason for which a bounty closed.
            assert list(reasons) == [("b1", "budget_spent"), ("b2", None), ("b3", None)]
            # Only the evidence columns of 0009 and the site check columns of 0010 differ.
            assert added_columns(connection) == {
                ("claims", "evidence_hash"),
                ("claims", "evidence_bundle"),
                *SITE_CHECK_COLUMNS,
            }
            command.upgrade(config, "0009")
            assert added_columns(connection) == SITE_CHECK_COLUMNS
            command.downgrade(config, "0008")
            claim_columns = inspect(connection).get_columns("claims", schema=schema)
            assert not {"evidence_hash", "evidence_bundle"} & {c["name"] for c in claim_columns}
            command.downgrade(config, "0007")
            columns = {
                c["name"] for c in inspect(connection).get_columns("bounties", schema=schema)
            }
            assert "closed_reason" not in columns
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_migration_0006_downgrade_survives_an_unfunded_and_a_paid_claim_for_one_fault():
    schema = f"arsune_final_fingerprint_test_{uuid4().hex}"
    config = Config("alembic.ini")
    insert = text("""INSERT INTO claims (id, bounty_id, hunter_account, claim_type, urls, details,
        explanation, reproduction_steps, fingerprint, final_fingerprint, status, outcome,
        evidence, created_at, updated_at) VALUES (:id, 'b1', :id, 'broken_link', '[]', '{}', 'x',
        '[]', 'same-fault', :final, :status, 'proven', '[]', now(), now())""")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.execute(
                text("""INSERT INTO bounties (id, url, origin, owner_account, proof_token, status,
                    reward_amount, bond_amount, fee_bps, budget_amount, budget_target,
                    created_at, updated_at) VALUES ('b1', 'https://site.test/',
                    'https://site.test:443', 'owner.near', 'token', 'open', 1, 1, 0, 0, 1,
                    now(), now())""")
            )
            # An unfunded claim holds no final fingerprint; a later paid claim holds it.
            connection.execute(insert, {"id": "unfunded", "final": None, "status": "unfunded"})
            connection.execute(insert, {"id": "paid", "final": "same-fault", "status": "paid"})
            command.downgrade(config, "0005")
            rows = connection.execute(text("SELECT id, outcome FROM claims ORDER BY id"))
            assert list(rows) == [("paid", "proven"), ("unfunded", "unfunded")]
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_migration_0010_adds_the_site_check_and_downgrade_opens_paused_bounties():
    schema = f"arsune_site_check_test_{uuid4().hex}"
    config = Config("alembic.ini")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}"')
            config.attributes["connection"] = connection
            command.upgrade(config, "0009")
            connection.execute(
                text("""INSERT INTO bounties (id, url, origin, owner_account, proof_token, status,
                    reward_amount, bond_amount, fee_bps, budget_amount, budget_target,
                    created_at, updated_at) VALUES ('b1', 'https://site.test/',
                    'https://site.test:443', 'owner.near', 'token', 'open', 1, 1, 0, 0, 1,
                    now(), now())""")
            )
            command.upgrade(config, "0010")
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
            row = connection.execute(
                text("SELECT site_check_job_id, paused_reason, unchecked_claims FROM bounties")
            ).one()
            assert tuple(row) == (None, None, 0)
            connection.execute(
                text("""INSERT INTO jobs (id, kind, url, mode, inject_demo, status, generation,
                    created_at, updated_at, deadline, checkpoint, progress, comparison_mapping,
                    incident_input) VALUES ('j1', 'site_check', 'https://site.test/', 'scripted',
                    false, 'queued', 0, now(), now(), now(), '{}', '{}', '{}', '{}')""")
            )
            connection.execute(text("INSERT INTO pending_actions VALUES ('j1:scan', 'j1', now())"))
            connection.execute(
                text(
                    "UPDATE bounties SET status = 'paused', paused_reason = 'x', site_check_job_id = 'j1'"
                )
            )
            command.downgrade(config, "0009")
            assert connection.execute(text("SELECT status FROM bounties")).scalar() == "open"
            assert connection.execute(text("SELECT status FROM jobs")).scalar() == "failed"
            assert not connection.execute(text("SELECT count(*) FROM pending_actions")).scalar()
            assert added_columns(connection) == SITE_CHECK_COLUMNS
            command.upgrade(config, "head")
            assert not compare_metadata(MigrationContext.configure(connection), Base.metadata)
    finally:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
