from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace

import pytest

from tests.support import load_module

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


class FakeCursor:
    def __init__(
        self,
        connection: FakeConnection,
        executed_sql: list[str],
        autocommit_at_execute: list[bool],
    ) -> None:
        self._connection = connection
        self._executed_sql = executed_sql
        self._autocommit_at_execute = autocommit_at_execute

    def execute(self, statement: str) -> None:
        self._autocommit_at_execute.append(self._connection.autocommit)
        self._executed_sql.append(statement)

    def close(self) -> None:
        return None


class FakeConnection:
    """Fake DBAPI connection that records every autocommit assignment.

    The listener under test must not rely on a session commit to persist
    `SET search_path` (a pooled connection's first checkout may never
    commit), so this fake lets the test observe the exact autocommit
    True/False sequence around the statement instead of just its final
    value.
    """

    def __init__(
        self, executed_sql: list[str], autocommit_at_execute: list[bool]
    ) -> None:
        self._autocommit = False
        self.autocommit_assignments: list[bool] = []
        self._executed_sql = executed_sql
        self._autocommit_at_execute = autocommit_at_execute

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self._autocommit = value
        self.autocommit_assignments.append(value)

    def cursor(self) -> FakeCursor:
        return FakeCursor(self, self._executed_sql, self._autocommit_at_execute)


def test_connect_listener_commits_search_path_via_autocommit_toggle(
    monkeypatch: pytest.MonkeyPatch,
):
    engine_kwargs: dict[str, object] = {}
    listener: dict[str, Callable[[object, object | None], None]] = {}

    def fake_create_async_engine(*args, **kwargs):
        engine_kwargs.update(kwargs)
        return SimpleNamespace(sync_engine=object())

    def fake_listens_for(target, identifier: str):
        assert identifier == 'connect'

        def decorator(fn):
            listener['handler'] = fn
            return fn

        return decorator

    monkeypatch.setenv('STOCKAPP_DATABASE_SCHEMA', 'stock')
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
    executed_sql: list[str] = []
    autocommit_at_execute: list[bool] = []
    connection = FakeConnection(executed_sql, autocommit_at_execute)

    handler(connection, None)

    assert executed_sql == ['SET search_path TO "stock", public']
    assert autocommit_at_execute == [True]
    assert connection.autocommit_assignments == [True, False]
    assert connection.autocommit is False
