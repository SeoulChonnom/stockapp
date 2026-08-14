from __future__ import annotations

import hashlib
import json
import logging
import os
import traceback
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, pool, text

from alembic import command
from app.core.settings import Settings
from app.db import migration_runner, schema_manifest
from app.db.schema_manifest import (
    canonicalize_migration_05_legacy_compatibility,
    manifest_mismatch_sections,
)

REPOSITORY_ROOT = Path(__file__).parents[2]
MANIFEST_PATH = (
    REPOSITORY_ROOT / 'db' / 'alembic' / 'manifests' / '20260731_schema_manifest.json'
)
# NOTE: literal, not derived from ScriptDirectory — update this whenever a new
# alembic revision becomes the head, so this test independently catches a
# broken/forked revision chain instead of trivially agreeing with production code.
LATEST_ALEMBIC_HEAD = '20260814_03_article_similarity_groups'


def _canonical_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))


def _settings(**overrides) -> Settings:
    values = {
        'app_env': 'development',
        'database_url': (
            'postgresql+psycopg://migration_user:'
            'private_password@db.example.test:5432/slcn'
        ),
        'database_schema': 'stock',
        'database_migration_enabled': True,
        'database_migration_lock_timeout_seconds': 5,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_schema_qualified_data_type_is_normalized():
    class FakeResult:
        def mappings(self):
            return self

        def all(self):
            return [{'data_type': '"stock"."market_type_enum"'}]

    class FakeConnection:
        def execute(self, *_args, **_kwargs):
            return FakeResult()

    assert schema_manifest._rows_as_dicts(
        cast(Any, FakeConnection()),
        'SELECT data_type',
        schema='stock',
    ) == [{'data_type': 'market_type_enum'}]


class _FakeConnection:
    def __init__(self) -> None:
        self.commit_count = 0

    def commit(self) -> None:
        self.commit_count += 1


def _migration_05_legacy_manifest() -> dict:
    manifest = deepcopy(_canonical_manifest())
    guard_columns = {
        'chk_market_index_daily_source_date_present': 'source_date',
        'chk_market_index_daily_expected_session_date_present': (
            'expected_session_date'
        ),
        'chk_market_index_daily_session_close_at_present': 'session_close_at',
    }
    for column in manifest['columns']:
        if (
            column['table_name'] == 'market_index_daily'
            and column['column_name'] in guard_columns.values()
        ):
            column['not_null'] = False
    for constraint in manifest['constraints']:
        if (
            constraint['table_name'] == 'market_index_daily'
            and constraint['constraint_name']
            == 'chk_market_index_daily_source_not_future'
        ):
            constraint['definition'] = (
                'CHECK (source_date IS NULL '
                'OR expected_session_date IS NULL '
                'OR source_date <= expected_session_date)'
            )
    for constraint_name, column_name in guard_columns.items():
        manifest['constraints'].append(
            {
                'constraint_name': constraint_name,
                'constraint_type': 'c',
                'definition': f'CHECK (({column_name} IS NOT NULL)) NOT VALID',
                'deferrable': False,
                'initially_deferred': False,
                'table_name': 'market_index_daily',
                'validated': False,
            }
        )
    manifest['constraints'].sort(
        key=lambda item: (item['table_name'], item['constraint_name'])
    )
    return manifest


def test_exact_canonical_manifest_is_accepted(monkeypatch: pytest.MonkeyPatch):
    canonical = _canonical_manifest()
    monkeypatch.setattr(
        migration_runner,
        'collect_schema_manifest',
        lambda *_args, **_kwargs: deepcopy(canonical),
    )

    assert migration_runner._legacy_manifest_mismatches(object(), schema='stock') == ()


def test_corruption_in_nonrepresentative_function_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
):
    corrupted = deepcopy(_canonical_manifest())
    corrupted['functions'][0]['definition'] += ' SELECT 1;'
    monkeypatch.setattr(
        migration_runner,
        'collect_schema_manifest',
        lambda *_args, **_kwargs: corrupted,
    )

    assert migration_runner._legacy_manifest_mismatches(object(), schema='stock') == (
        'functions',
    )


def test_migration_05_exact_legacy_nullability_guards_are_accepted():
    canonical = _canonical_manifest()
    legacy = _migration_05_legacy_manifest()

    normalized = canonicalize_migration_05_legacy_compatibility(
        legacy,
        canonical_manifest=canonical,
    )

    assert manifest_mismatch_sections(normalized, canonical) == ()


@pytest.mark.parametrize(
    ('metadata_name', 'invalid_value'),
    [
        ('validated', False),
        ('deferrable', True),
        ('initially_deferred', True),
    ],
)
def test_migration_05_source_constraint_requires_exact_metadata(
    metadata_name: str,
    invalid_value: bool,
):
    canonical = _canonical_manifest()
    legacy = _migration_05_legacy_manifest()
    source_constraint = next(
        constraint
        for constraint in legacy['constraints']
        if constraint['constraint_name'] == 'chk_market_index_daily_source_not_future'
    )
    source_constraint[metadata_name] = invalid_value

    normalized = canonicalize_migration_05_legacy_compatibility(
        legacy,
        canonical_manifest=canonical,
    )

    assert manifest_mismatch_sections(normalized, canonical) == ('constraints',)


def test_migration_05_legacy_variant_requires_all_exact_guards():
    canonical = _canonical_manifest()
    legacy = _migration_05_legacy_manifest()
    legacy['constraints'] = [
        constraint
        for constraint in legacy['constraints']
        if constraint['constraint_name']
        != 'chk_market_index_daily_session_close_at_present'
    ]

    normalized = canonicalize_migration_05_legacy_compatibility(
        legacy,
        canonical_manifest=canonical,
    )

    assert set(manifest_mismatch_sections(normalized, canonical)) == {
        'columns',
        'constraints',
    }


@pytest.mark.parametrize(
    ('version_table_exists', 'schema_object_count', 'expected_calls'),
    [
        (True, 99, [('upgrade', 'head')]),
        (False, 0, [('upgrade', 'head')]),
    ],
)
def test_upgrade_locked_upgrades_versioned_and_fresh_databases(
    monkeypatch: pytest.MonkeyPatch,
    version_table_exists: bool,
    schema_object_count: int,
    expected_calls: list[tuple[str, str]],
):
    connection = _FakeConnection()
    calls: list[tuple[str, str]] = []

    class FakeInspector:
        def has_table(self, table_name: str, *, schema: str) -> bool:
            assert table_name == 'alembic_version'
            assert schema == 'stock'
            return version_table_exists

    monkeypatch.setattr(migration_runner, 'inspect', lambda _: FakeInspector())
    monkeypatch.setattr(
        migration_runner,
        '_schema_object_count',
        lambda *_args, **_kwargs: schema_object_count,
    )
    monkeypatch.setattr(
        migration_runner.command,
        'upgrade',
        lambda _config, target: calls.append(('upgrade', target)),
    )

    head = migration_runner._upgrade_locked(connection, settings=_settings())

    assert calls == expected_calls
    assert connection.commit_count == 1
    assert head == LATEST_ALEMBIC_HEAD


def test_upgrade_locked_stamps_matching_legacy_manifest_before_upgrade(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = _FakeConnection()
    calls: list[tuple[str, str]] = []

    class FakeInspector:
        def has_table(self, _table_name: str, *, schema: str) -> bool:
            assert schema == 'stock'
            return False

    monkeypatch.setattr(migration_runner, 'inspect', lambda _: FakeInspector())
    monkeypatch.setattr(
        migration_runner,
        '_schema_object_count',
        lambda *_args, **_kwargs: 42,
    )
    monkeypatch.setattr(
        migration_runner,
        '_legacy_manifest_mismatches',
        lambda *_args, **_kwargs: (),
    )
    monkeypatch.setattr(
        migration_runner.command,
        'stamp',
        lambda _config, target: calls.append(('stamp', target)),
    )
    monkeypatch.setattr(
        migration_runner.command,
        'upgrade',
        lambda _config, target: calls.append(('upgrade', target)),
    )

    head = migration_runner._upgrade_locked(connection, settings=_settings())

    assert calls == [
        ('stamp', migration_runner.ALEMBIC_BASELINE_REVISION),
        ('upgrade', 'head'),
    ]
    assert connection.commit_count == 1
    assert head == LATEST_ALEMBIC_HEAD


def test_upgrade_locked_refuses_mismatched_non_empty_schema(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = _FakeConnection()

    class FakeInspector:
        def has_table(self, _table_name: str, *, schema: str) -> bool:
            assert schema == 'stock'
            return False

    monkeypatch.setattr(migration_runner, 'inspect', lambda _: FakeInspector())
    monkeypatch.setattr(
        migration_runner,
        '_schema_object_count',
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        migration_runner,
        '_legacy_manifest_mismatches',
        lambda *_args, **_kwargs: ('columns', 'triggers'),
    )
    monkeypatch.setattr(
        migration_runner.command,
        'stamp',
        lambda *_args, **_kwargs: pytest.fail('partial schema must not be stamped'),
    )

    with pytest.raises(
        migration_runner.DatabaseMigrationError,
        match='Mismatched manifest sections: columns, triggers',
    ):
        migration_runner._upgrade_locked(connection, settings=_settings())


def test_advisory_lock_retries_until_acquired(
    monkeypatch: pytest.MonkeyPatch,
):
    class Result:
        def __init__(self, acquired: bool):
            self.acquired = acquired

        def scalar_one(self) -> bool:
            return self.acquired

    class Connection:
        def __init__(self):
            self.results = iter([False, False, True])
            self.commits = 0

        def execute(self, *_args, **_kwargs):
            return Result(next(self.results))

        def commit(self):
            self.commits += 1

    connection = Connection()
    now = iter([0.0, 0.1, 0.2])
    sleeps: list[float] = []
    monkeypatch.setattr(migration_runner, 'monotonic', lambda: next(now))
    monkeypatch.setattr(migration_runner, 'sleep', sleeps.append)

    migration_runner._acquire_advisory_lock(connection, timeout_seconds=1)

    assert connection.commits == 3
    assert sleeps == [0.25, 0.25]


def test_advisory_lock_timeout_is_bounded(monkeypatch: pytest.MonkeyPatch):
    class Result:
        def scalar_one(self) -> bool:
            return False

    class Connection:
        def execute(self, *_args, **_kwargs):
            return Result()

        def commit(self):
            return None

    now = iter([0.0, 0.4, 1.1])
    monkeypatch.setattr(migration_runner, 'monotonic', lambda: next(now))
    monkeypatch.setattr(migration_runner, 'sleep', lambda _seconds: None)

    with pytest.raises(
        migration_runner.DatabaseMigrationError,
        match='Timed out waiting',
    ):
        migration_runner._acquire_advisory_lock(
            Connection(),
            timeout_seconds=1,
        )


def test_startup_migration_holds_one_lock_across_prepare_and_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    connection = object()
    events: list[tuple[str, object]] = []

    class ConnectionContext:
        def __enter__(self):
            return connection

        def __exit__(self, *_args):
            return None

    class FakeEngine:
        def connect(self):
            return ConnectionContext()

    monkeypatch.setattr(
        migration_runner,
        '_acquire_advisory_lock',
        lambda value, **_kwargs: events.append(('lock', value)),
    )
    monkeypatch.setattr(
        migration_runner,
        '_prepare_schema',
        lambda value, **_kwargs: events.append(('prepare', value)),
    )

    def upgrade(value, **_kwargs):
        events.append(('upgrade', value))
        return 'future_revision'

    monkeypatch.setattr(migration_runner, '_upgrade_locked', upgrade)
    monkeypatch.setattr(
        migration_runner,
        '_release_advisory_lock',
        lambda value: events.append(('unlock', value)),
    )
    caplog.set_level('INFO', logger='app.db.migrations')

    migration_runner.run_startup_migrations(_settings(), engine=FakeEngine())

    assert events == [
        ('lock', connection),
        ('prepare', connection),
        ('upgrade', connection),
        ('unlock', connection),
    ]
    assert 'revision=future_revision' in caplog.text


def test_startup_migration_releases_lock_and_removes_unsafe_traceback_cause(
    monkeypatch: pytest.MonkeyPatch,
):
    connection = object()
    unlocked: list[object] = []

    class ConnectionContext:
        def __enter__(self):
            return connection

        def __exit__(self, *_args):
            return None

    class FakeEngine:
        def connect(self):
            return ConnectionContext()

    monkeypatch.setattr(
        migration_runner,
        '_acquire_advisory_lock',
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        migration_runner,
        '_prepare_schema',
        lambda *_args, **_kwargs: None,
    )

    def fail_upgrade(*_args, **_kwargs):
        raise RuntimeError(
            'postgresql://admin:secret@db.example.test/stock is unavailable'
        )

    monkeypatch.setattr(migration_runner, '_upgrade_locked', fail_upgrade)
    monkeypatch.setattr(
        migration_runner,
        '_release_advisory_lock',
        lambda value: unlocked.append(value),
    )

    with pytest.raises(migration_runner.DatabaseMigrationError) as exc_info:
        migration_runner.run_startup_migrations(_settings(), engine=FakeEngine())

    formatted_traceback = ''.join(traceback.format_exception(exc_info.value))
    assert unlocked == [connection]
    assert 'Database migration failed (RuntimeError)' in formatted_traceback
    assert 'postgresql://' not in formatted_traceback
    assert 'secret' not in formatted_traceback
    assert exc_info.value.__cause__ is None


def test_startup_migration_can_be_disabled_without_creating_an_engine(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        migration_runner,
        'create_engine',
        lambda *_args, **_kwargs: pytest.fail('engine must not be created'),
    )

    migration_runner.run_startup_migrations(_settings(database_migration_enabled=False))


def test_startup_migration_rejects_noncanonical_schema():
    with pytest.raises(
        migration_runner.DatabaseMigrationError,
        match='STOCKAPP_DATABASE_SCHEMA=stock',
    ):
        migration_runner.run_startup_migrations(
            _settings(database_schema='tenant_schema')
        )


def test_alembic_chain_has_one_squashed_baseline_head():
    config = Config(str(REPOSITORY_ROOT / 'alembic.ini'))
    script = ScriptDirectory.from_config(config)

    # Pinned to LATEST_ALEMBIC_HEAD (a literal, not derived from ScriptDirectory)
    # so this test independently catches a forked/broken revision chain.
    assert script.get_heads() == [LATEST_ALEMBIC_HEAD]
    baseline = script.get_revision(migration_runner.ALEMBIC_BASELINE_REVISION)
    assert baseline is not None
    assert baseline.down_revision is None


def test_alembic_incremental_step_diagnostics_use_stock_schema():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    config = Config(str(REPOSITORY_ROOT / 'alembic.ini'))
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)

    def run_migration(action, target: str) -> None:
        with engine.connect() as connection:
            connection.execute(text('SET search_path TO "$user", public'))
            connection.commit()
            config.attributes['connection'] = connection
            action(config, target)

    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        run_migration(command.upgrade, '20260807_01_step_run')
        run_migration(command.upgrade, LATEST_ALEMBIC_HEAD)

        with engine.connect() as connection:
            columns = (
                connection.execute(
                    text(
                        """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'stock'
                      AND table_name = 'batch_job_step_run'
                      AND column_name IN ('error_message', 'error_log')
                    ORDER BY column_name
                    """
                    )
                )
                .scalars()
                .all()
            )
        assert columns == ['error_log', 'error_message']

        run_migration(command.downgrade, '20260807_01_step_run')

        with engine.connect() as connection:
            remaining_columns = (
                connection.execute(
                    text(
                        """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = 'stock'
                      AND table_name = 'batch_job_step_run'
                      AND column_name IN ('error_message', 'error_log')
                    """
                    )
                )
                .scalars()
                .all()
            )
        assert remaining_columns == []
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_alembic_offline_sql_contains_schema_before_version_table(capsys):
    config = Config(str(REPOSITORY_ROOT / 'alembic.ini'))
    config.set_main_option(
        'sqlalchemy.url',
        'postgresql+psycopg://unused:unused@localhost/unused',
    )

    command.upgrade(config, 'head', sql=True)

    output = capsys.readouterr().out
    assert 'CREATE SCHEMA IF NOT EXISTS "stock"' in output
    assert output.index('CREATE SCHEMA IF NOT EXISTS "stock"') < output.index(
        'CREATE TABLE stock.alembic_version'
    )
    assert 'CREATE TABLE batch_job' in output
    assert (
        'ALTER TABLE stock.batch_job_step_run ADD COLUMN error_message TEXT' in output
    )
    assert 'ALTER TABLE stock.batch_job_step_run ADD COLUMN error_log TEXT' in output


def test_alembic_sets_search_path_before_any_unqualified_ddl(capsys):
    # Revisions write unqualified DDL. Only the frozen baseline sets search_path
    # inline, so env.py must set it for every revision that follows — otherwise a
    # database already stamped at the baseline resolves against "$user", public.
    config = Config(str(REPOSITORY_ROOT / 'alembic.ini'))
    config.set_main_option(
        'sqlalchemy.url',
        'postgresql+psycopg://unused:unused@localhost/unused',
    )

    command.upgrade(config, 'head', sql=True)

    output = capsys.readouterr().out
    assert 'SET search_path TO "stock", public' in output
    assert output.index('SET search_path TO "stock", public') < output.index(
        'CREATE TABLE batch_job'
    )
    # The head revision repeats it locally so replaying it through another runner
    # cannot land batch_job_step_run in public.
    assert output.index('SET LOCAL search_path TO stock, public') < output.index(
        'CREATE TABLE batch_job_step_run'
    )


def test_safe_failure_detail_reports_sqlstate_without_the_driver_message():
    class FakeDiagnostic:
        schema_name = None
        table_name = 'batch_job'
        column_name = None
        constraint_name = None
        datatype_name = None

    class FakeDriverError(Exception):
        sqlstate = '42P01'
        diag = FakeDiagnostic()

    driver_error = FakeDriverError(
        'relation "batch_job" does not exist on '
        'postgresql://admin:secret@db.example.test/stock'
    )
    try:
        raise driver_error
    except FakeDriverError as cause:
        wrapped = RuntimeError('(FakeDriverError) admin:secret leaked here')
        wrapped.__cause__ = cause

    detail = migration_runner._safe_failure_detail(wrapped)

    assert (
        detail
        == 'chain=RuntimeError<-FakeDriverError sqlstate=42P01 table_name=batch_job'
    )
    assert 'secret' not in detail
    assert 'postgresql://' not in detail


def test_startup_migration_logs_the_safe_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    class ConnectionContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return None

    class FakeEngine:
        def connect(self):
            return ConnectionContext()

    def fail_upgrade(*_args, **_kwargs):
        raise RuntimeError('postgresql://admin:secret@db.example.test/stock is down')

    for name in ('_acquire_advisory_lock', '_prepare_schema', '_release_advisory_lock'):
        monkeypatch.setattr(migration_runner, name, lambda *_a, **_k: None)
    monkeypatch.setattr(migration_runner, '_upgrade_locked', fail_upgrade)

    with (
        caplog.at_level(logging.ERROR, logger='app.db.migrations'),
        pytest.raises(migration_runner.DatabaseMigrationError),
    ):
        migration_runner.run_startup_migrations(_settings(), engine=FakeEngine())

    assert 'event=upgrade_failed' in caplog.text
    assert 'chain=RuntimeError' in caplog.text
    assert 'secret' not in caplog.text


def test_frozen_baseline_and_catalog_manifest_are_immutable():
    baseline = REPOSITORY_ROOT / 'db' / 'alembic' / 'baselines' / '20260731_schema.sql'

    assert (
        hashlib.sha256(baseline.read_bytes()).hexdigest()
        == '9c16cbe0bb880aabc9fe5c035e8b1e4825bf9b3e521fc49b538fb2cd90470ec0'
    )
    assert (
        hashlib.sha256(MANIFEST_PATH.read_bytes()).hexdigest()
        == '09a6a5dc85679134dab60cd494743e8e177f7f79b413385beebac6611985ee61'
    )
