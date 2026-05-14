from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support import load_module

settings_module = load_module('app.core.settings')


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


def test_settings_loads_env_from_stockapp_directory_independent_of_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv('STOCKAPP_APP_ENV', raising=False)
    monkeypatch.delenv('app_env', raising=False)
    monkeypatch.delenv('STOCKAPP_CORS_ALLOWED_ORIGINS', raising=False)
    monkeypatch.delenv('cors_allowed_origins', raising=False)
    env_file = tmp_path / '.env'
    env_file.write_text(
        '\n'.join(
            [
                'STOCKAPP_APP_ENV=development',
                'STOCKAPP_CORS_ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setitem(settings_module.Settings.model_config, 'env_file', env_file)
    monkeypatch.chdir(Path('/'))
    settings = settings_module.get_settings()

    assert settings.is_development is True
    assert settings.cors_allowed_origins_list == [
        'http://localhost:5173',
        'http://127.0.0.1:5173',
    ]


@pytest.mark.parametrize(
    'schema',
    [
        'stock; DROP TABLE stock; --',
        'stock public',
        '1stock',
    ],
)
def test_settings_reject_invalid_database_schema_identifiers(schema: str):
    with pytest.raises(ValidationError, match='database_schema'):
        settings_module.Settings(database_schema=schema)


def test_settings_accept_valid_database_schema_identifier():
    settings = settings_module.Settings(database_schema='stock')

    assert settings.database_schema == 'stock'
