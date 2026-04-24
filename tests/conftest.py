from __future__ import annotations

from typing import Any

import pytest  # pyright: ignore[reportMissingImports]

from tests.support import (
    BUSINESS_DATE,
    JWT_ISSUER,
    JWT_TEST_SECRET,
    JWT_REFRESH_TOKEN_TYPE,
    build_archive_item_payload,
    build_archive_list_payload,
    build_batch_job_detail_payload,
    build_batch_job_list_payload,
    build_batch_run_payload,
    build_test_jwt_subject,
    build_cluster_detail_payload,
    build_daily_page_payload,
    build_page_article_link_rows,
    build_page_cluster_rows,
    build_page_index_rows,
    build_page_market_rows,
    build_page_snapshot_row,
    build_cluster_article_rows,
    build_cluster_row,
    build_raw_article_rows,
    build_processed_article_rows,
    load_module,
    mint_test_jwt,
)


@pytest.fixture(autouse=True)
def configure_jwt_auth_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("STOCKAPP_APP_ENV", "production")
    monkeypatch.setenv("STOCKAPP_CORS_ALLOWED_ORIGINS", '["http://localhost:5173"]')
    monkeypatch.setenv("STOCKAPP_JWT_SECRET", JWT_TEST_SECRET)
    monkeypatch.setenv("STOCKAPP_JWT_ALGORITHM", "HS512")
    monkeypatch.setenv("STOCKAPP_JWT_ISSUER", JWT_ISSUER)
    monkeypatch.setenv("STOCKAPP_JWT_ACCESS_AUDIENCES", '["slcn-platform"]')

    settings_module = load_module("app.core.settings")
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


@pytest.fixture
def sample_business_date() -> Any:
    return BUSINESS_DATE


@pytest.fixture
def sample_daily_page_payload() -> dict[str, Any]:
    return build_daily_page_payload()


@pytest.fixture
def sample_archive_item_payload() -> dict[str, Any]:
    return build_archive_item_payload()


@pytest.fixture
def sample_archive_list_payload() -> dict[str, Any]:
    return build_archive_list_payload()


@pytest.fixture
def sample_cluster_detail_payload() -> dict[str, Any]:
    return build_cluster_detail_payload()


@pytest.fixture
def sample_batch_run_payload() -> dict[str, Any]:
    return build_batch_run_payload()


@pytest.fixture
def sample_batch_job_list_payload() -> dict[str, Any]:
    return build_batch_job_list_payload()


@pytest.fixture
def sample_batch_job_detail_payload() -> dict[str, Any]:
    return build_batch_job_detail_payload()


@pytest.fixture
def sample_page_snapshot_row() -> dict[str, Any]:
    return build_page_snapshot_row()


@pytest.fixture
def sample_page_market_rows() -> list[dict[str, Any]]:
    return build_page_market_rows()


@pytest.fixture
def sample_page_index_rows() -> list[dict[str, Any]]:
    return build_page_index_rows()


@pytest.fixture
def sample_page_cluster_rows() -> list[dict[str, Any]]:
    return build_page_cluster_rows()


@pytest.fixture
def sample_page_article_link_rows() -> list[dict[str, Any]]:
    return build_page_article_link_rows()


@pytest.fixture
def sample_cluster_row() -> dict[str, Any]:
    return build_cluster_row()


@pytest.fixture
def sample_cluster_article_rows() -> list[dict[str, Any]]:
    return build_cluster_article_rows()


@pytest.fixture
def sample_processed_article_rows() -> list[dict[str, Any]]:
    return build_processed_article_rows()


@pytest.fixture
def sample_raw_article_rows() -> list[dict[str, Any]]:
    return build_raw_article_rows()


@pytest.fixture
def app():
    main_module = load_module("app.main")
    app_instance = main_module.create_app()
    if hasattr(app_instance, "dependency_overrides"):
        app_instance.dependency_overrides.clear()
    yield app_instance
    if hasattr(app_instance, "dependency_overrides"):
        app_instance.dependency_overrides.clear()


@pytest.fixture
def client(app):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def jwt_subject() -> str:
    return build_test_jwt_subject()


@pytest.fixture
def jwt_access_token_factory(jwt_subject: str):
    def factory(**overrides: Any) -> str:
        params = {
            "subject": jwt_subject,
            "username": "stockapp-user",
            "roles": ["USER"],
        }
        params.update(overrides)
        return mint_test_jwt(**params)

    return factory


@pytest.fixture
def jwt_access_token(jwt_access_token_factory) -> str:
    return jwt_access_token_factory()


@pytest.fixture
def jwt_refresh_token(jwt_access_token_factory) -> str:
    return jwt_access_token_factory(token_type=JWT_REFRESH_TOKEN_TYPE)
