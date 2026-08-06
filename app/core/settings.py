import base64
import binascii
import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.db.identifiers import validate_postgres_identifier


class Settings(BaseSettings):
    app_name: str = 'Market Daily Brief API'
    app_version: str = '0.1.0'
    app_env: str = Field(
        default='production',
        validation_alias=AliasChoices('STOCKAPP_APP_ENV', 'app_env'),
    )
    request_id_header: str = 'X-Request-Id'
    database_url: str = Field(
        default='postgresql+psycopg://mcp_doc:mcp_doc_password@localhost:5432/slcn',
    )
    database_schema: str = 'stock'
    database_pool_size: int = Field(
        default=5,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_DATABASE_POOL_SIZE', 'database_pool_size'
        ),
    )
    database_max_overflow: int = Field(
        default=10,
        ge=0,
        validation_alias=AliasChoices(
            'STOCKAPP_DATABASE_MAX_OVERFLOW', 'database_max_overflow'
        ),
    )
    database_pool_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_DATABASE_POOL_TIMEOUT_SECONDS',
            'database_pool_timeout_seconds',
        ),
    )
    database_migration_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            'STOCKAPP_DATABASE_MIGRATION_ENABLED',
            'database_migration_enabled',
        ),
    )
    database_migration_lock_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_DATABASE_MIGRATION_LOCK_TIMEOUT_SECONDS',
            'database_migration_lock_timeout_seconds',
        ),
    )
    batch_worker_poll_interval_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_WORKER_POLL_INTERVAL_SECONDS',
            'batch_worker_poll_interval_seconds',
        ),
    )
    batch_worker_heartbeat_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_WORKER_HEARTBEAT_SECONDS',
            'batch_worker_heartbeat_seconds',
        ),
    )
    batch_worker_lease_seconds: int = Field(
        default=120,
        ge=2,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_WORKER_LEASE_SECONDS',
            'batch_worker_lease_seconds',
        ),
    )
    batch_worker_max_attempts: int = Field(
        default=3,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS',
            'batch_worker_max_attempts',
        ),
    )
    batch_worker_retry_delay_seconds: int = Field(
        default=30,
        ge=0,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_WORKER_RETRY_DELAY_SECONDS',
            'batch_worker_retry_delay_seconds',
        ),
    )
    batch_max_clusters_per_market: int = Field(
        default=12,
        ge=2,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_MAX_CLUSTERS_PER_MARKET',
            'batch_max_clusters_per_market',
        ),
    )
    batch_clustering_processed_article_limit: int = Field(
        default=5000,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_CLUSTERING_PROCESSED_ARTICLE_LIMIT',
            'batch_clustering_processed_article_limit',
        ),
    )
    batch_startup_recovery_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            'STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED',
            'batch_startup_recovery_enabled',
        ),
    )
    auth_stub_token: str = 'dev-token'
    jwt_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            'SLCN_JWT_SECRETKEY',
            'STOCKAPP_JWT_SECRET',
            'jwt_secret',
        ),
    )
    jwt_algorithm: str = Field(
        default='HS512',
        validation_alias=AliasChoices(
            'SLCN_JWT_ALGORITHM',
            'STOCKAPP_JWT_ALGORITHM',
            'jwt_algorithm',
        ),
    )
    jwt_issuer: str = Field(
        default='slcnapp',
        validation_alias=AliasChoices(
            'SLCN_JWT_ISSUER',
            'STOCKAPP_JWT_ISSUER',
            'jwt_issuer',
        ),
    )
    jwt_access_audiences: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ['slcn-platform'],
        validation_alias=AliasChoices(
            'SLCN_JWT_ACCESS_AUDIENCES',
            'STOCKAPP_JWT_ACCESS_AUDIENCES',
            'jwt_access_audiences',
        ),
    )
    cors_allowed_origins: str = Field(
        default='',
        validation_alias=AliasChoices(
            'STOCKAPP_CORS_ALLOWED_ORIGINS',
            'cors_allowed_origins',
        ),
    )
    naver_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices('STOCKAPP_NAVER_CLIENT_ID', 'naver_client_id'),
    )
    naver_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            'STOCKAPP_NAVER_CLIENT_SECRET', 'naver_client_secret'
        ),
    )
    naver_news_base_url: str = Field(
        default='https://openapi.naver.com/v1/search/news.json',
        validation_alias=AliasChoices(
            'STOCKAPP_NAVER_NEWS_BASE_URL', 'naver_news_base_url'
        ),
    )
    naver_news_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            'STOCKAPP_NAVER_NEWS_TIMEOUT_SECONDS',
            'naver_news_timeout_seconds',
        ),
    )
    naver_news_collection_overlap_minutes: int = Field(
        default=10,
        ge=0,
        le=29,
        validation_alias=AliasChoices(
            'STOCKAPP_NAVER_NEWS_COLLECTION_OVERLAP_MINUTES',
            'naver_news_collection_overlap_minutes',
        ),
    )
    naver_news_collection_backfill_max_days: int = Field(
        default=7,
        ge=1,
        le=30,
        validation_alias=AliasChoices(
            'STOCKAPP_NAVER_NEWS_COLLECTION_BACKFILL_MAX_DAYS',
            'naver_news_collection_backfill_max_days',
        ),
    )
    article_crawl_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            'STOCKAPP_ARTICLE_CRAWL_TIMEOUT_SECONDS',
            'article_crawl_timeout_seconds',
        ),
    )
    article_crawl_user_agent: str = Field(
        default='stockapp-batch/0.1',
        validation_alias=AliasChoices(
            'STOCKAPP_ARTICLE_CRAWL_USER_AGENT',
            'article_crawl_user_agent',
        ),
    )
    article_crawl_concurrency_limit: int = Field(
        default=4,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_ARTICLE_CRAWL_CONCURRENCY_LIMIT',
            'article_crawl_concurrency_limit',
        ),
    )
    yfinance_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            'STOCKAPP_YFINANCE_TIMEOUT_SECONDS',
            'yfinance_timeout_seconds',
        ),
    )
    market_session_data_grace_minutes: int = Field(
        default=30,
        ge=0,
        validation_alias=AliasChoices(
            'STOCKAPP_MARKET_SESSION_DATA_GRACE_MINUTES',
            'market_session_data_grace_minutes',
        ),
    )
    llm_provider: str = Field(
        default='google-genai',
        validation_alias=AliasChoices('STOCKAPP_LLM_PROVIDER', 'llm_provider'),
    )
    llm_model: str = Field(
        default='gemini-3.1-flash-lite',
        validation_alias=AliasChoices('STOCKAPP_LLM_MODEL', 'llm_model'),
    )
    llm_temperature: float = Field(
        default=0.2,
        validation_alias=AliasChoices('STOCKAPP_LLM_TEMPERATURE', 'llm_temperature'),
    )
    llm_max_retries: int = Field(
        default=2,
        ge=0,
        validation_alias=AliasChoices('STOCKAPP_LLM_MAX_RETRIES', 'llm_max_retries'),
    )
    llm_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_TIMEOUT_SECONDS', 'llm_timeout_seconds'
        ),
    )
    llm_concurrency_limit: int = Field(
        default=2,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_CONCURRENCY_LIMIT', 'llm_concurrency_limit'
        ),
    )
    llm_requests_per_minute: int = Field(
        default=12,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_REQUESTS_PER_MINUTE',
            'llm_requests_per_minute',
        ),
    )
    llm_tokens_per_minute: int = Field(
        default=250_000,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_TOKENS_PER_MINUTE',
            'llm_tokens_per_minute',
        ),
    )
    llm_quota_project_id: str = Field(
        default='default',
        min_length=1,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_QUOTA_PROJECT_ID',
            'llm_quota_project_id',
        ),
    )
    llm_retry_base_delay_seconds: float = Field(
        default=5.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_RETRY_BASE_DELAY_SECONDS',
            'llm_retry_base_delay_seconds',
        ),
    )
    llm_retry_max_delay_seconds: float = Field(
        default=300.0,
        gt=0,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_RETRY_MAX_DELAY_SECONDS',
            'llm_retry_max_delay_seconds',
        ),
    )
    llm_retry_jitter_ratio: float = Field(
        default=0.2,
        ge=0,
        le=1,
        validation_alias=AliasChoices(
            'STOCKAPP_LLM_RETRY_JITTER_RATIO',
            'llm_retry_jitter_ratio',
        ),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices('STOCKAPP_GEMINI_API_KEY', 'gemini_api_key'),
    )

    model_config = SettingsConfigDict(
        env_prefix='STOCKAPP_',
        env_file=Path(__file__).resolve().parents[2] / '.env',
        extra='ignore',
    )

    @field_validator('app_env', mode='before')
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator('database_schema', mode='before')
    @classmethod
    def normalize_database_schema(cls, value: object) -> object:
        if isinstance(value, str):
            return validate_postgres_identifier(value, kind='schema')
        return value

    @field_validator('batch_worker_lease_seconds')
    @classmethod
    def validate_batch_worker_lease(cls, value: int, info) -> int:
        heartbeat = info.data.get('batch_worker_heartbeat_seconds', 30)
        if value <= heartbeat:
            raise ValueError(
                'batch_worker_lease_seconds must exceed batch_worker_heartbeat_seconds'
            )
        return value

    @field_validator('cors_allowed_origins', mode='before')
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> object:
        if isinstance(value, list):
            return ','.join(
                str(origin).strip() for origin in value if str(origin).strip()
            )
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith('['):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return value
                if isinstance(parsed, list):
                    return ','.join(
                        str(origin).strip() for origin in parsed if str(origin).strip()
                    )
        return value

    @field_validator('jwt_algorithm', 'jwt_issuer', mode='before')
    @classmethod
    def normalize_auth_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator('jwt_access_audiences', mode='before')
    @classmethod
    def parse_jwt_access_audiences(cls, value: object) -> object:
        if isinstance(value, list):
            return [
                str(audience).strip() for audience in value if str(audience).strip()
            ]
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            if stripped.startswith('['):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return [
                        audience.strip()
                        for audience in stripped.split(',')
                        if audience.strip()
                    ]
                if isinstance(parsed, list):
                    return [
                        str(audience).strip()
                        for audience in parsed
                        if str(audience).strip()
                    ]
            return [
                audience.strip() for audience in stripped.split(',') if audience.strip()
            ]
        return value

    @property
    def is_development(self) -> bool:
        return self.app_env == 'development'

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(',')
            if origin.strip()
        ]

    def validate_for_app_startup(self) -> None:
        if not self.app_env == 'production':
            return

        unsafe_reasons = [
            *self._unsafe_database_reasons(),
            *self._unsafe_jwt_reasons(),
        ]
        if unsafe_reasons:
            raise RuntimeError(
                'Unsafe production configuration: ' + '; '.join(unsafe_reasons)
            )

    def _unsafe_database_reasons(self) -> list[str]:
        parsed = urlparse(self.database_url)
        reasons: list[str] = []
        if not parsed.scheme.startswith('postgresql'):
            reasons.append('database_url must use PostgreSQL in production')
        if parsed.hostname in {'localhost', '127.0.0.1', '::1'}:
            reasons.append('database_url must not target localhost in production')
        if parsed.username == 'mcp_doc' or parsed.password == 'mcp_doc_password':
            reasons.append('database_url must not use bundled default credentials')
        return reasons

    def _unsafe_jwt_reasons(self) -> list[str]:
        secret = self.jwt_secret.strip() if isinstance(self.jwt_secret, str) else ''
        if not secret:
            return ['jwt_secret is required in production']

        padding = '=' * (-len(secret) % 4)
        try:
            decoded_secret = base64.urlsafe_b64decode(f'{secret}{padding}')
        except ValueError, binascii.Error:
            return ['jwt_secret must be base64url encoded']
        if len(decoded_secret) < 32:
            return ['jwt_secret must decode to at least 32 bytes']
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ['Settings', 'get_settings']
