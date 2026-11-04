"""Check migration history in an isolated PostgreSQL schema."""

from uuid import uuid4

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from arsune.db import Base, engine
from sqlalchemy import inspect


def test_empty_schema_upgrade_downgrade_upgrade():
    schema = f"arsune_migration_test_{uuid4().hex}"
    config = Config("alembic.ini")
    tables = {"jobs", "attempts", "events", "records", "pending_actions"}
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
