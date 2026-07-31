from __future__ import annotations

import os
from logging.config import fileConfig

from sqlalchemy import Connection, create_engine, pool, text

from alembic import context
from app.db.identifiers import quote_postgres_identifier, validate_postgres_identifier

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _database_url() -> str:
    configured_url = config.get_main_option('sqlalchemy.url')
    if configured_url:
        return configured_url
    database_url = os.getenv('STOCKAPP_DATABASE_URL')
    if not database_url:
        raise RuntimeError('STOCKAPP_DATABASE_URL is required for Alembic migrations.')
    return database_url


def _version_table_schema() -> str:
    configured_schema = config.attributes.get('version_table_schema')
    schema = configured_schema or os.getenv('STOCKAPP_DATABASE_SCHEMA', 'stock')
    validated_schema = validate_postgres_identifier(str(schema), kind='schema')
    if validated_schema != 'stock':
        raise RuntimeError(
            'Alembic migrations support only the canonical "stock" schema.'
        )
    return validated_schema


def _configure_context(connection: Connection, *, schema: str) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        include_schemas=True,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    schema = _version_table_schema()
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        version_table_schema=schema,
        include_schemas=True,
        compare_type=True,
        transaction_per_migration=True,
    )
    with context.begin_transaction():
        context.execute(
            'CREATE SCHEMA IF NOT EXISTS '
            + quote_postgres_identifier(schema, kind='schema')
        )
        context.run_migrations()


def run_migrations_online() -> None:
    schema = _version_table_schema()
    supplied_connection = config.attributes.get('connection')
    if supplied_connection is not None:
        _configure_context(supplied_connection, schema=schema)
        return

    connectable = create_engine(
        _database_url(),
        poolclass=pool.NullPool,
    )
    try:
        with connectable.connect() as connection:
            connection.execute(
                text(
                    'CREATE SCHEMA IF NOT EXISTS '
                    + quote_postgres_identifier(schema, kind='schema')
                )
            )
            connection.commit()
            _configure_context(connection, schema=schema)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
