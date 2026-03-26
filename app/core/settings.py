from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Market Daily Brief API"
    app_version: str = "0.1.0"
    request_id_header: str = "X-Request-Id"
    database_url: str = Field(
        default="postgresql+psycopg://mcp_doc:mcp_doc_password@localhost:5432/slcn",
    )
    database_schema: str = "stock"
    auth_stub_token: str = "dev-token"

    model_config = SettingsConfigDict(
        env_prefix="STOCKAPP_",
        env_file=".env",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


__all__ = ["Settings", "get_settings"]
