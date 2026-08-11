from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from tests.support import load_module

identifiers_module = load_module('app.db.identifiers')
session_module = load_module('app.db.session')
settings_module = load_module('app.core.settings')


@pytest.fixture(autouse=True)
def clear_caches():
    settings_module.get_settings.cache_clear()
    session_module.get_async_engine.cache_clear()
    session_module.get_session_maker.cache_clear()
    yield
    settings_module.get_settings.cache_clear()
    session_module.get_async_engine.cache_clear()
    session_module.get_session_maker.cache_clear()


@pytest.mark.parametrize(
    ('identifier', 'kind'),
    [
        ('stock; DROP TABLE stock; --', 'schema'),
        ('stock public', 'schema'),
        ('1stock', 'schema'),
    ],
)
def test_validate_postgres_identifier_rejects_malicious_schema_values(
    identifier: str,
    kind: str,
):
    with pytest.raises(ValueError, match='Invalid PostgreSQL'):
        identifiers_module.validate_postgres_identifier(identifier, kind=kind)


def test_qualify_db_identifier_preserves_valid_stock_schema():
    assert (
        identifiers_module.qualify_db_identifier(
            'market_daily_page',
            schema='stock',
        )
        == 'stock.market_daily_page'
    )


def test_build_search_path_sql_quotes_validated_schema():
    assert (
        identifiers_module.build_search_path_sql('stock')
        == 'SET search_path TO "stock", public'
    )


def test_get_async_engine_sets_validated_quoted_search_path(
    monkeypatch: pytest.MonkeyPatch,
):
    executed_sql: list[str] = []
    engine_kwargs: dict[str, object] = {}
    listener: dict[str, Callable[[DummyConnection, object | None], None]] = {}

    class DummyCursor:
        def execute(self, statement: str) -> None:
            executed_sql.append(statement)

        def close(self) -> None:
            return None

    class DummyConnection:
        def __init__(self) -> None:
            self.autocommit = False

        def cursor(self) -> DummyCursor:
            return DummyCursor()

    class DummyEngine:
        def __init__(self) -> None:
            self.sync_engine = object()

    def fake_create_async_engine(*args, **kwargs):
        engine_kwargs.update(kwargs)
        return DummyEngine()

    def fake_listens_for(target, identifier: str):
        assert identifier == 'connect'

        def decorator(fn):
            listener['handler'] = fn
            return fn

        return decorator

    monkeypatch.setenv('STOCKAPP_DATABASE_SCHEMA', 'stock')
    monkeypatch.setenv('STOCKAPP_DATABASE_POOL_SIZE', '7')
    monkeypatch.setenv('STOCKAPP_DATABASE_MAX_OVERFLOW', '2')
    monkeypatch.setenv('STOCKAPP_DATABASE_POOL_TIMEOUT_SECONDS', '4.5')
    monkeypatch.setattr(
        session_module,
        'create_async_engine',
        fake_create_async_engine,
    )
    monkeypatch.setattr(
        session_module,
        'event',
        SimpleNamespace(listens_for=fake_listens_for),
    )

    session_module.get_async_engine()

    handler = listener['handler']
    connection = DummyConnection()
    handler(connection, None)
    assert executed_sql == ['SET search_path TO "stock", public']
    assert connection.autocommit is False
    assert engine_kwargs['pool_pre_ping'] is True
    assert engine_kwargs['pool_size'] == 7
    assert engine_kwargs['max_overflow'] == 2
    assert engine_kwargs['pool_timeout'] == 4.5
