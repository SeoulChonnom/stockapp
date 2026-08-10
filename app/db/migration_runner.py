from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterator
from pathlib import Path
from time import monotonic, sleep
from typing import Final

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, create_engine, inspect, text

from alembic import command
from app.core.settings import Settings
from app.db.identifiers import quote_postgres_identifier
from app.db.schema_manifest import (
    canonicalize_migration_05_legacy_compatibility,
    collect_schema_manifest,
    manifest_mismatch_sections,
)

LOGGER = logging.getLogger('app.db.migrations')

ALEMBIC_BASELINE_REVISION: Final = '20260731_00_baseline'
MIGRATION_ADVISORY_LOCK_ID: Final = 7_621_073_120_260_731
CANONICAL_SCHEMA: Final = 'stock'

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_ALEMBIC_INI = _REPOSITORY_ROOT / 'alembic.ini'
_SCHEMA_MANIFEST = (
    _REPOSITORY_ROOT / 'db' / 'alembic' / 'manifests' / '20260731_schema_manifest.json'
)
# PostgreSQL diagnostic fields that only ever carry catalog identifiers, never
# row data — safe to log verbatim alongside the SQLSTATE.
_SAFE_DIAGNOSTIC_FIELDS: Final = (
    'schema_name',
    'table_name',
    'column_name',
    'constraint_name',
    'datatype_name',
)


class DatabaseMigrationError(RuntimeError):
    """Raised when automatic database migration cannot complete safely."""


def _exception_chain(exception: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exception
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _safe_failure_detail(exception: BaseException) -> str:
    """Describe a migration failure without repeating any driver message.

    Driver messages can embed the connection URL, its credentials, or offending
    row values, so only the exception classes, the SQLSTATE, and PostgreSQL's
    diagnostic object names — all catalog identifiers — are reported.
    """
    classes: list[str] = []
    details: list[str] = []

    def record(field: str, value: object) -> None:
        entry = f'{field}={value}'
        if value and entry not in details:
            details.append(entry)

    for error in _exception_chain(exception):
        classes.append(type(error).__name__)
        record('sqlstate', getattr(error, 'sqlstate', None))
        diagnostic = getattr(error, 'diag', None)
        for field in _SAFE_DIAGNOSTIC_FIELDS:
            record(field, getattr(diagnostic, field, None))
    return ' '.join(['chain=' + '<-'.join(classes), *details])


def _load_schema_manifest() -> dict:
    try:
        return json.loads(_SCHEMA_MANIFEST.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatabaseMigrationError(
            f'Canonical schema manifest is unavailable ({type(exc).__name__}).'
        ) from None


def _legacy_manifest_mismatches(
    connection: Connection,
    *,
    schema: str,
) -> tuple[str, ...]:
    canonical_manifest = _load_schema_manifest()
    actual_manifest = collect_schema_manifest(connection, schema=schema)
    compatible_manifest = canonicalize_migration_05_legacy_compatibility(
        actual_manifest,
        canonical_manifest=canonical_manifest,
    )
    return manifest_mismatch_sections(compatible_manifest, canonical_manifest)


def _schema_object_count(connection: Connection, *, schema: str) -> int:
    result = connection.execute(
        text(
            """
            SELECT count(*)
            FROM (
                SELECT class_.oid
                FROM pg_class AS class_
                JOIN pg_namespace AS namespace_
                  ON namespace_.oid = class_.relnamespace
                WHERE namespace_.nspname = :schema
                  AND class_.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
                UNION ALL
                SELECT type_.oid
                FROM pg_type AS type_
                JOIN pg_namespace AS namespace_
                  ON namespace_.oid = type_.typnamespace
                WHERE namespace_.nspname = :schema
                  AND type_.typtype = 'e'
                UNION ALL
                SELECT procedure_.oid
                FROM pg_proc AS procedure_
                JOIN pg_namespace AS namespace_
                  ON namespace_.oid = procedure_.pronamespace
                WHERE namespace_.nspname = :schema
            ) AS schema_objects
            """
        ),
        {'schema': schema},
    )
    return int(result.scalar_one())


def _alembic_config(
    *,
    database_url: str,
    connection: Connection,
    schema: str,
) -> Config:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option('sqlalchemy.url', database_url.replace('%', '%%'))
    config.attributes['connection'] = connection
    config.attributes['version_table_schema'] = schema
    return config


def _prepare_schema(connection: Connection, *, schema: str) -> None:
    connection.execute(
        text(
            'CREATE SCHEMA IF NOT EXISTS '
            + quote_postgres_identifier(schema, kind='schema')
        )
    )
    connection.commit()


def _acquire_advisory_lock(
    connection: Connection,
    *,
    timeout_seconds: float,
) -> None:
    deadline = monotonic() + timeout_seconds
    while True:
        acquired = bool(
            connection.execute(
                text('SELECT pg_try_advisory_lock(:lock_id)'),
                {'lock_id': MIGRATION_ADVISORY_LOCK_ID},
            ).scalar_one()
        )
        connection.commit()
        if acquired:
            return

        remaining_seconds = deadline - monotonic()
        if remaining_seconds <= 0:
            raise DatabaseMigrationError(
                'Timed out waiting for the database migration advisory lock.'
            )
        sleep(min(0.25, remaining_seconds))


def _release_advisory_lock(connection: Connection) -> None:
    if connection.in_transaction():
        connection.rollback()
    connection.execute(
        text('SELECT pg_advisory_unlock(:lock_id)'),
        {'lock_id': MIGRATION_ADVISORY_LOCK_ID},
    )
    connection.commit()


def _upgrade_locked(
    connection: Connection,
    *,
    settings: Settings,
) -> str:
    schema = settings.database_schema
    config = _alembic_config(
        database_url=settings.database_url,
        connection=connection,
        schema=schema,
    )
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    if head_revision is None:
        raise DatabaseMigrationError('Alembic migration head is not configured.')

    version_table_exists = inspect(connection).has_table(
        'alembic_version',
        schema=schema,
    )
    if version_table_exists:
        connection.commit()
        command.upgrade(config, 'head')
        return head_revision

    if _schema_object_count(connection, schema=schema) == 0:
        connection.commit()
        command.upgrade(config, 'head')
        return head_revision

    mismatches = _legacy_manifest_mismatches(connection, schema=schema)
    if mismatches:
        raise DatabaseMigrationError(
            'Existing stock schema is not a recognized legacy head; '
            'automatic stamp was refused. Mismatched manifest sections: '
            + ', '.join(mismatches)
        )

    connection.commit()
    command.stamp(config, ALEMBIC_BASELINE_REVISION)
    command.upgrade(config, 'head')
    return head_revision


def run_startup_migrations(
    settings: Settings,
    *,
    engine: Engine | None = None,
) -> None:
    """Apply Alembic migrations under a session-level PostgreSQL advisory lock."""
    if not settings.database_migration_enabled:
        LOGGER.info('database_migration event=disabled')
        return
    if settings.database_schema != CANONICAL_SCHEMA:
        raise DatabaseMigrationError(
            'Automatic migrations support only STOCKAPP_DATABASE_SCHEMA=stock.'
        )

    migration_engine = engine or create_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
    )
    owns_engine = engine is None
    try:
        with migration_engine.connect() as connection:
            _acquire_advisory_lock(
                connection,
                timeout_seconds=settings.database_migration_lock_timeout_seconds,
            )
            try:
                _prepare_schema(connection, schema=settings.database_schema)
                LOGGER.info('database_migration event=upgrade_started')
                head_revision = _upgrade_locked(connection, settings=settings)
                LOGGER.info(
                    'database_migration event=upgrade_completed revision=%s',
                    head_revision,
                )
            finally:
                _release_advisory_lock(connection)
    except DatabaseMigrationError as exc:
        # Every DatabaseMigrationError message is an authored constant, so it is
        # safe to log in full.
        LOGGER.error('database_migration event=upgrade_failed reason=%s', exc)
        raise
    except Exception as exc:
        detail = _safe_failure_detail(exc)
        LOGGER.error('database_migration event=upgrade_failed %s', detail)
        raise DatabaseMigrationError(
            f'Database migration failed ({type(exc).__name__}). {detail}'
        ) from None
    finally:
        if owns_engine:
            migration_engine.dispose()


async def run_startup_migrations_async(settings: Settings) -> None:
    """Run blocking Alembic and psycopg work outside the event loop."""
    await asyncio.to_thread(run_startup_migrations, settings)


__all__ = [
    'ALEMBIC_BASELINE_REVISION',
    'DatabaseMigrationError',
    'run_startup_migrations',
    'run_startup_migrations_async',
]
