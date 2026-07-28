from __future__ import annotations

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
    monkeypatch.setitem(settings_module.Settings.model_config, 'env_file', tmp_path / '.env')
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


def test_ready_endpoint_is_removed():
    app = main_module.create_app()

    with TestClient(app) as client:
        response = client.get('/stock/api/ready')

    assert response.status_code == 404
