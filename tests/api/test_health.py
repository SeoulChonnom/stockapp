from __future__ import annotations

import logging
import traceback
from io import StringIO

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tests.support import JWT_TEST_SECRET, load_module

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient

health_module = load_module('app.api.health')
main_module = load_module('app.main')
settings_module = load_module('app.core.settings')


@pytest.fixture(autouse=True)
def configure_safe_app_startup(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setitem(
        settings_module.Settings.model_config, 'env_file', tmp_path / '.env'
    )
    monkeypatch.setenv('STOCKAPP_APP_ENV', 'production')
    monkeypatch.setenv(
        'STOCKAPP_DATABASE_URL',
        'postgresql+psycopg://app:secret@db.example.com:5432/slcn',
    )
    monkeypatch.setenv('STOCKAPP_JWT_SECRET', JWT_TEST_SECRET)
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


class HealthyDbSession:
    def __init__(self):
        self.executed = False

    async def execute(self, statement):
        self.executed = True
        return statement


class FailingDbSession:
    async def execute(self, statement):
        raise SQLAlchemyError('database unavailable')


def test_health_checks_database_without_auth():
    db_session = HealthyDbSession()

    async def override_db_session():
        yield db_session

    app = main_module.create_app()
    app.dependency_overrides[health_module.get_db_session] = override_db_session

    with TestClient(app) as client:
        response = client.get('/stock/api/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
    assert db_session.executed is True


def test_request_id_header_rejects_unsafe_reflection():
    async def override_db_session():
        yield HealthyDbSession()

    app = main_module.create_app()
    app.dependency_overrides[health_module.get_db_session] = override_db_session

    with TestClient(app) as client:
        response = client.get(
            '/stock/api/health', headers={'X-Request-Id': 'bad request id'}
        )

    assert response.status_code == 200
    assert response.headers['X-Request-Id'].startswith('req-')
    assert response.headers['X-Request-Id'] != 'bad request id'


def test_request_id_header_preserves_safe_values():
    async def override_db_session():
        yield HealthyDbSession()

    app = main_module.create_app()
    app.dependency_overrides[health_module.get_db_session] = override_db_session

    with TestClient(app) as client:
        response = client.get(
            '/stock/api/health', headers={'X-Request-Id': 'req.safe-123:abc'}
        )

    assert response.status_code == 200
    assert response.headers['X-Request-Id'] == 'req.safe-123:abc'


def test_health_returns_error_envelope_when_database_unavailable():
    async def override_db_session():
        yield FailingDbSession()

    app = main_module.create_app()
    app.dependency_overrides[health_module.get_db_session] = override_db_session

    with TestClient(app) as client:
        response = client.get('/stock/api/health')

    assert response.status_code == 503
    payload = response.json()
    assert payload['success'] is False
    assert payload['error'] == {
        'code': 'HEALTH_DATABASE_UNAVAILABLE',
        'message': 'Database is unavailable.',
    }
    assert payload['meta']['requestId'].startswith('req-')


def test_startup_recovery_drains_durable_queue_once(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeScheduler:
        def __init__(self):
            self.drain_calls = 0
            self.shutdown_calls = 0

        def start_drain(self):
            self.drain_calls += 1

        async def shutdown(self):
            self.shutdown_calls += 1

    scheduler = FakeScheduler()
    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'true')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        lambda: scheduler,
    )
    app = main_module.create_app()

    async def override_db_session():
        yield HealthyDbSession()

    app.dependency_overrides[health_module.get_db_session] = override_db_session

    with TestClient(app) as client:
        response = client.get('/stock/api/health')

    assert response.status_code == 200
    assert scheduler.drain_calls == 1
    assert scheduler.shutdown_calls == 1


def test_startup_recovery_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeScheduler:
        def __init__(self):
            self.drain_calls = 0
            self.shutdown_calls = 0

        def start_drain(self):
            self.drain_calls += 1

        async def shutdown(self):
            self.shutdown_calls += 1

    scheduler = FakeScheduler()
    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'false')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        lambda: scheduler,
    )
    app = main_module.create_app()

    with TestClient(app):
        pass

    assert scheduler.drain_calls == 0
    assert scheduler.shutdown_calls == 1


def test_fastapi_lifespan_emits_one_batch_startup_log_per_app(
    monkeypatch: pytest.MonkeyPatch,
):
    class FakeScheduler:
        def start_drain(self):
            raise AssertionError('startup recovery must be disabled')

        async def shutdown(self):
            return None

    output = StringIO()
    console_handler = logging.StreamHandler(output)
    batch_logger = logging.getLogger('app.batch')
    uvicorn_logger = logging.getLogger('uvicorn')
    uvicorn_error_logger = logging.getLogger('uvicorn.error')
    monkeypatch.setattr(batch_logger, 'handlers', [])
    monkeypatch.setattr(batch_logger, 'propagate', True)
    monkeypatch.setattr(uvicorn_logger, 'handlers', [console_handler])
    monkeypatch.setattr(uvicorn_error_logger, 'handlers', [])
    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'false')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        FakeScheduler,
    )

    with TestClient(main_module.create_app()):
        pass
    with TestClient(main_module.create_app()):
        pass

    assert output.getvalue().count('batch_runtime event=startup') == 2
    assert batch_logger.handlers.count(console_handler) == 1


def test_startup_recovery_failure_logs_safely_and_does_not_abort_app(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    class FailingScheduler:
        def start_drain(self):
            raise RuntimeError('postgresql://admin:secret@db.example.com/stock')

        async def shutdown(self):
            return None

    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'true')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        FailingScheduler,
    )
    caplog.set_level(logging.ERROR, logger='app.batch.runtime')

    with TestClient(main_module.create_app()):
        pass

    assert 'exception_class=RuntimeError' in caplog.text
    assert 'postgresql://' not in caplog.text
    assert 'secret' not in caplog.text


def test_database_migration_completes_before_batch_scheduler_starts(
    monkeypatch: pytest.MonkeyPatch,
):
    events: list[str] = []

    async def migrate(_settings):
        events.append('migration')

    class FakeScheduler:
        def start_drain(self):
            events.append('drain')

        async def shutdown(self):
            events.append('shutdown')

    def build_scheduler():
        events.append('scheduler')
        return FakeScheduler()

    monkeypatch.setenv('STOCKAPP_DATABASE_MIGRATION_ENABLED', 'true')
    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'true')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(main_module, 'run_startup_migrations_async', migrate)
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        build_scheduler,
    )

    with TestClient(main_module.create_app()):
        pass

    assert events == ['migration', 'scheduler', 'drain', 'shutdown']


def test_database_migration_failure_aborts_before_scheduler_and_logs_safely(
    monkeypatch: pytest.MonkeyPatch,
    caplog,
):
    async def fail_migration(_settings):
        raise RuntimeError('postgresql://admin:secret@db.example.com/stock')

    monkeypatch.setenv('STOCKAPP_DATABASE_MIGRATION_ENABLED', 'true')
    settings_module.get_settings.cache_clear()
    monkeypatch.setattr(
        main_module,
        'run_startup_migrations_async',
        fail_migration,
    )
    monkeypatch.setattr(
        main_module,
        'get_in_process_batch_scheduler',
        lambda: pytest.fail('scheduler must not be created'),
    )
    caplog.set_level(logging.ERROR, logger='app.db.migrations')

    with pytest.raises(
        RuntimeError,
        match='Database migration failed during startup',
    ) as exc_info:
        with TestClient(main_module.create_app()):
            pass

    formatted_traceback = ''.join(traceback.format_exception(exc_info.value))
    assert 'exception_class=RuntimeError' in caplog.text
    assert 'postgresql://' not in caplog.text
    assert 'secret' not in caplog.text
    assert 'postgresql://' not in formatted_traceback
    assert 'secret' not in formatted_traceback
    assert exc_info.value.__cause__ is None


def test_ready_endpoint_is_removed():
    app = main_module.create_app()

    with TestClient(app) as client:
        response = client.get('/stock/api/ready')

    assert response.status_code == 404
