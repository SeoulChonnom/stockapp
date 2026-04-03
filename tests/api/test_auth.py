from __future__ import annotations

import pytest

from tests.support import load_module

auth_module = load_module("app.api.deps.auth")
settings_module = load_module("app.core.settings")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_get_current_user_accepts_dev_stub_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STOCKAPP_APP_ENV", "development")
    monkeypatch.setenv("STOCKAPP_CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("STOCKAPP_AUTH_STUB_TOKEN", "dev-token")
    user = await auth_module.get_current_user("Bearer dev-token")

    assert user.user_id == "stub-user"
    assert user.token == "dev-token"


@pytest.mark.asyncio
async def test_get_current_user_rejects_wrong_dev_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STOCKAPP_APP_ENV", "development")
    monkeypatch.setenv("STOCKAPP_CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("STOCKAPP_AUTH_STUB_TOKEN", "dev-token")
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.get_current_user("Bearer wrong-token")

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_bearer_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STOCKAPP_APP_ENV", "development")
    monkeypatch.setenv("STOCKAPP_CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("STOCKAPP_AUTH_STUB_TOKEN", "dev-token")
    with pytest.raises(auth_module.HTTPException) as exc_info:
        await auth_module.get_current_user("dev-token")

    assert exc_info.value.status_code == 401
