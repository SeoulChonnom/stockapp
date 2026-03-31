from functools import lru_cache
import json

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Market Daily Brief API"
    app_version: str = "0.1.0"
    app_env: str = Field(
        default="production",
        validation_alias=AliasChoices("STOCKAPP_APP_ENV", "app_env"),
    )
    request_id_header: str = "X-Request-Id"
    database_url: str = Field(
        default="postgresql+psycopg://mcp_doc:mcp_doc_password@localhost:5432/slcn",
    )
    database_schema: str = "stock"
    auth_stub_token: str = "dev-token"
    cors_allowed_origins: str = Field(
        default="",
        validation_alias=AliasChoices(
            "STOCKAPP_CORS_ALLOWED_ORIGINS",
            "cors_allowed_origins",
        ),
    )
    naver_client_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("STOCKAPP_NAVER_CLIENT_ID", "naver_client_id"),
    )
    naver_client_secret: str | None = Field(
        default=None,
        validation_alias=AliasChoices("STOCKAPP_NAVER_CLIENT_SECRET", "naver_client_secret"),
    )
    naver_news_base_url: str = Field(
        default="https://openapi.naver.com/v1/search/news.json",
        validation_alias=AliasChoices("STOCKAPP_NAVER_NEWS_BASE_URL", "naver_news_base_url"),
    )
    naver_news_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "STOCKAPP_NAVER_NEWS_TIMEOUT_SECONDS",
            "naver_news_timeout_seconds",
        ),
    )
    article_crawl_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "STOCKAPP_ARTICLE_CRAWL_TIMEOUT_SECONDS",
            "article_crawl_timeout_seconds",
        ),
    )
    article_crawl_user_agent: str = Field(
        default="stockapp-batch/0.1",
        validation_alias=AliasChoices(
            "STOCKAPP_ARTICLE_CRAWL_USER_AGENT",
            "article_crawl_user_agent",
        ),
    )
    yfinance_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices(
            "STOCKAPP_YFINANCE_TIMEOUT_SECONDS",
            "yfinance_timeout_seconds",
        ),
    )
    llm_provider: str = Field(
        default="google-genai",
        validation_alias=AliasChoices("STOCKAPP_LLM_PROVIDER", "llm_provider"),
    )
    llm_model: str = Field(
        default="gemini-3.1-flash-lite",
        validation_alias=AliasChoices("STOCKAPP_LLM_MODEL", "llm_model"),
    )
    llm_temperature: float = Field(
        default=0.2,
        validation_alias=AliasChoices("STOCKAPP_LLM_TEMPERATURE", "llm_temperature"),
    )
    llm_max_retries: int = Field(
        default=2,
        validation_alias=AliasChoices("STOCKAPP_LLM_MAX_RETRIES", "llm_max_retries"),
    )
    gemini_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("STOCKAPP_GEMINI_API_KEY", "gemini_api_key"),
    )

    model_config = SettingsConfigDict(
        env_prefix="STOCKAPP_",
        env_file=".env",
        extra="ignore",
    )

    @field_validator("app_env", mode="before")
    @classmethod
    def normalize_app_env(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def parse_cors_allowed_origins(cls, value: object) -> object:
        if isinstance(value, list):
            return ",".join(str(origin).strip() for origin in value if str(origin).strip())
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                try:
                    parsed = json.loads(stripped)
                except json.JSONDecodeError:
                    return value
                if isinstance(parsed, list):
                    return ",".join(str(origin).strip() for origin in parsed if str(origin).strip())
        return value

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
