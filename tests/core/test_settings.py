from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.support import JWT_TEST_SECRET, load_module

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


def test_settings_defaults_to_gemini_3_1_flash_lite(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_LLM_MODEL', raising=False)
    monkeypatch.delenv('llm_model', raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.llm_model == 'gemini-3.1-flash-lite'


@pytest.mark.parametrize(
    ('raw_value', 'expected'),
    [
        ('slcn-platform', ['slcn-platform']),
        ('slcn-platform,stockapp', ['slcn-platform', 'stockapp']),
        ('["slcn-platform", "stockapp"]', ['slcn-platform', 'stockapp']),
    ],
)
def test_settings_loads_jwt_access_audiences_from_env(
    raw_value: str,
    expected: list[str],
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_JWT_ACCESS_AUDIENCES', raw_value)

    settings = settings_module.Settings(_env_file=None)

    assert settings.jwt_access_audiences == expected


def test_settings_loads_jwt_access_audiences_from_slcn_env_alias(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_JWT_ACCESS_AUDIENCES', raising=False)
    monkeypatch.setenv('SLCN_JWT_ACCESS_AUDIENCES', 'slcn-platform')

    settings = settings_module.Settings(_env_file=None)

    assert settings.jwt_access_audiences == ['slcn-platform']


@pytest.mark.parametrize(
    'key_name',
    [
        'gemini_api_key',
        'stockapp_gemini_api_key',
    ],
)
def test_settings_accepts_lowercase_gemini_key_names_from_env_file(
    key_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.delenv('STOCKAPP_GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('gemini_api_key', raising=False)
    env_file = tmp_path / '.env'
    env_file.write_text(f'{key_name}=test-gemini-key\n', encoding='utf-8')

    settings = settings_module.Settings(_env_file=env_file)

    assert settings.gemini_api_key == 'test-gemini-key'


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


def test_production_startup_validation_rejects_default_database_url(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_DATABASE_URL', raising=False)
    monkeypatch.delenv('database_url', raising=False)
    settings = settings_module.Settings(jwt_secret=JWT_TEST_SECRET)

    with pytest.raises(RuntimeError, match='database_url'):
        settings.validate_for_app_startup()


@pytest.mark.parametrize('jwt_secret', [None, '', 'not-base64url!'])
def test_production_startup_validation_rejects_unsafe_jwt_secret(
    jwt_secret: str | None,
):
    settings = settings_module.Settings(
        database_url='postgresql+psycopg://app:secret@db.example.com:5432/slcn',
        jwt_secret=jwt_secret,
    )

    with pytest.raises(RuntimeError, match='jwt_secret'):
        settings.validate_for_app_startup()


def test_production_startup_validation_accepts_safe_required_configuration():
    settings = settings_module.Settings(
        database_url='postgresql+psycopg://app:secret@db.example.com:5432/slcn',
        jwt_secret=JWT_TEST_SECRET,
    )

    settings.validate_for_app_startup()


def test_development_startup_validation_allows_local_defaults():
    settings = settings_module.Settings(app_env='development')

    settings.validate_for_app_startup()


def test_test_startup_validation_allows_local_defaults():
    settings = settings_module.Settings(app_env='test')

    settings.validate_for_app_startup()


def test_settings_accepts_batch_concurrency_limits():
    settings = settings_module.Settings(
        app_env='development',
        article_crawl_concurrency_limit=3,
        llm_concurrency_limit=2,
        llm_requests_per_minute=7,
    )

    assert settings.article_crawl_concurrency_limit == 3
    assert settings.llm_concurrency_limit == 2
    assert settings.llm_requests_per_minute == 7


def test_settings_defaults_to_twelve_llm_requests_per_minute(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_LLM_REQUESTS_PER_MINUTE', raising=False)
    monkeypatch.delenv('llm_requests_per_minute', raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.llm_requests_per_minute == 12


def test_settings_loads_llm_requests_per_minute_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_LLM_REQUESTS_PER_MINUTE', '8')

    settings = settings_module.Settings(_env_file=None)

    assert settings.llm_requests_per_minute == 8


@pytest.mark.parametrize('requests_per_minute', [0, -1])
def test_settings_rejects_non_positive_llm_requests_per_minute(
    requests_per_minute: int,
):
    with pytest.raises(ValidationError, match='llm_requests_per_minute'):
        settings_module.Settings(llm_requests_per_minute=requests_per_minute)


def test_settings_rejects_negative_llm_max_retries():
    with pytest.raises(ValidationError, match='llm_max_retries'):
        settings_module.Settings(llm_max_retries=-1)


def test_settings_loads_durable_worker_timing_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_HEARTBEAT_SECONDS', '20')
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_LEASE_SECONDS', '90')
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS', '4')

    settings = settings_module.Settings(_env_file=None)

    assert settings.batch_worker_heartbeat_seconds == 20
    assert settings.batch_worker_lease_seconds == 90
    assert settings.batch_worker_max_attempts == 4


def test_settings_rejects_lease_not_longer_than_heartbeat():
    with pytest.raises(ValidationError, match='batch_worker_lease_seconds'):
        settings_module.Settings(
            batch_worker_heartbeat_seconds=30,
            batch_worker_lease_seconds=30,
        )
