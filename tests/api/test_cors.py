from __future__ import annotations

import pytest

from tests.support import load_module

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

settings_module = load_module("app.core.settings")
main_module = load_module("app.main")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


def test_cors_allows_dev_frontend_origin(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STOCKAPP_APP_ENV", "development")
    monkeypatch.setenv(
        "STOCKAPP_CORS_ALLOWED_ORIGINS",
        '["http://localhost:5173","http://127.0.0.1:5173"]',
    )
    app = main_module.create_app()

    with TestClient(app) as client:
        response = client.options(
            "/stock/api/pages/daily/latest",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
