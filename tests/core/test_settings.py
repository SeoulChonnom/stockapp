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


def test_database_migrations_are_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_DATABASE_MIGRATION_ENABLED', raising=False)
    monkeypatch.delenv('database_migration_enabled', raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.database_migration_enabled is True


def test_database_migrations_can_be_disabled_from_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_DATABASE_MIGRATION_ENABLED', 'false')

    settings = settings_module.Settings(_env_file=None)

    assert settings.database_migration_enabled is False


def test_database_migration_lock_timeout_loads_from_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS', '12.5')

    settings = settings_module.Settings(_env_file=None)

    assert settings.database_migration_lock_timeout_seconds == 12.5


@pytest.mark.parametrize('timeout_seconds', [0, -1])
def test_database_migration_lock_timeout_must_be_positive(
    timeout_seconds: float,
):
    with pytest.raises(
        ValidationError,
        match='database_migration_lock_timeout_seconds',
    ):
        settings_module.Settings(
            database_migration_lock_timeout_seconds=timeout_seconds
        )


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


def test_settings_defaults_to_twelve_clusters_per_market(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv('STOCKAPP_BATCH_MAX_CLUSTERS_PER_MARKET', raising=False)
    monkeypatch.delenv('batch_max_clusters_per_market', raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.batch_max_clusters_per_market == 12


def test_settings_loads_cluster_cap_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_BATCH_MAX_CLUSTERS_PER_MARKET', '8')

    settings = settings_module.Settings(_env_file=None)

    assert settings.batch_max_clusters_per_market == 8


def test_settings_rejects_cluster_cap_below_two():
    with pytest.raises(ValidationError, match='batch_max_clusters_per_market'):
        settings_module.Settings(batch_max_clusters_per_market=1)


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


def test_settings_loads_llm_tpm_and_durable_retry_policy_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_LLM_TOKENS_PER_MINUTE', '12345')
    monkeypatch.setenv('STOCKAPP_LLM_QUOTA_PROJECT_ID', 'project-a')
    monkeypatch.setenv('STOCKAPP_LLM_RETRY_BASE_DELAY_SECONDS', '7.5')
    monkeypatch.setenv('STOCKAPP_LLM_RETRY_MAX_DELAY_SECONDS', '90')
    monkeypatch.setenv('STOCKAPP_LLM_RETRY_JITTER_RATIO', '0.3')

    settings = settings_module.Settings(_env_file=None)

    assert settings.llm_tokens_per_minute == 12345
    assert settings.llm_quota_project_id == 'project-a'
    assert settings.llm_retry_base_delay_seconds == 7.5
    assert settings.llm_retry_max_delay_seconds == 90
    assert settings.llm_retry_jitter_ratio == 0.3


@pytest.mark.parametrize('tokens_per_minute', [0, -1])
def test_settings_rejects_non_positive_llm_tokens_per_minute(
    tokens_per_minute: int,
):
    with pytest.raises(ValidationError, match='llm_tokens_per_minute'):
        settings_module.Settings(llm_tokens_per_minute=tokens_per_minute)


def test_settings_loads_durable_worker_timing_from_env(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_HEARTBEAT_SECONDS', '20')
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_LEASE_SECONDS', '90')
    monkeypatch.setenv('STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS', '4')
    monkeypatch.setenv('STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED', 'true')

    settings = settings_module.Settings(_env_file=None)

    assert settings.batch_worker_heartbeat_seconds == 20
    assert settings.batch_worker_lease_seconds == 90
    assert settings.batch_worker_max_attempts == 4
    assert settings.batch_startup_recovery_enabled is True


def test_settings_rejects_lease_not_longer_than_heartbeat():
    with pytest.raises(ValidationError, match='batch_worker_lease_seconds'):
        settings_module.Settings(
            batch_worker_heartbeat_seconds=30,
            batch_worker_lease_seconds=30,
        )


def test_settings_loads_ollama_configuration_from_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_OLLAMA_BASE_URL', 'http://ollama.test:11434/')
    monkeypatch.setenv('STOCKAPP_OLLAMA_EMBED_MODEL', 'custom-embed')
    monkeypatch.setenv('STOCKAPP_OLLAMA_TIMEOUT_SECONDS', '12.5')
    monkeypatch.setenv('STOCKAPP_OLLAMA_MAX_RETRIES', '1')
    monkeypatch.setenv('STOCKAPP_SIMILARITY_INPUT_CHARS', '1024')

    settings = settings_module.Settings(_env_file=None)

    assert settings.ollama_base_url == 'http://ollama.test:11434/'
    assert settings.ollama_embed_model == 'custom-embed'
    assert settings.ollama_timeout_seconds == 12.5
    assert settings.ollama_max_retries == 1
    assert settings.similarity_input_chars == 1024


def test_settings_uses_ollama_defaults(monkeypatch: pytest.MonkeyPatch):
    for name in (
        'STOCKAPP_OLLAMA_BASE_URL',
        'STOCKAPP_OLLAMA_EMBED_MODEL',
        'STOCKAPP_OLLAMA_TIMEOUT_SECONDS',
        'STOCKAPP_OLLAMA_MAX_RETRIES',
        'STOCKAPP_SIMILARITY_INPUT_CHARS',
    ):
        monkeypatch.delenv(name, raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.ollama_base_url == 'http://localhost:11434'
    assert settings.ollama_embed_model == 'bge-m3'
    assert settings.ollama_timeout_seconds == 30
    assert settings.ollama_max_retries == 2
    assert settings.similarity_input_chars == 2048


@pytest.mark.parametrize(
    ('field_name', 'value'),
    [
        ('ollama_timeout_seconds', 0),
        ('ollama_timeout_seconds', -1),
        ('ollama_max_retries', -1),
        ('ollama_max_retries', 3),
        ('similarity_input_chars', 0),
        ('similarity_input_chars', -1),
    ],
)
def test_settings_rejects_invalid_ollama_bounds(field_name: str, value: object):
    with pytest.raises(ValidationError, match=field_name):
        settings_module.Settings(**{field_name: value})
