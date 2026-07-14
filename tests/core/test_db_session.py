from __future__ import annotations

import pytest

from tests.support import load_module

session_module = load_module('app.db.session')


@pytest.mark.asyncio
async def test_get_db_session_rolls_back_when_dependency_consumer_raises(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeSession:
        def __init__(self) -> None:
            self.rollback_called = False

        async def rollback(self) -> None:
            self.rollback_called = True

    class FakeSessionContext:
        def __init__(self, session: FakeSession) -> None:
            self.session = session
            self.exited = False

        async def __aenter__(self) -> FakeSession:
            return self.session

        async def __aexit__(self, exc_type, exc, traceback) -> None:
            self.exited = True

    fake_session = FakeSession()
    fake_context = FakeSessionContext(fake_session)
    monkeypatch.setattr(session_module, 'get_session_maker', lambda: lambda: fake_context)

    dependency = session_module.get_db_session()
    assert await anext(dependency) is fake_session

    with pytest.raises(RuntimeError, match='escaped'):
        await dependency.athrow(RuntimeError('escaped'))

    assert fake_session.rollback_called is True
    assert fake_context.exited is True
