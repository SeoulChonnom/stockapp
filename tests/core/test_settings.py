from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import load_module

settings_module = load_module("app.core.settings")


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


def test_settings_loads_env_from_stockapp_directory_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("STOCKAPP_APP_ENV", raising=False)
    monkeypatch.delenv("app_env", raising=False)
    monkeypatch.delenv("STOCKAPP_CORS_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("cors_allowed_origins", raising=False)
    monkeypatch.chdir(Path("/"))
    settings = settings_module.get_settings()

    assert settings.is_development is True
    assert settings.cors_allowed_origins_list == [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]
