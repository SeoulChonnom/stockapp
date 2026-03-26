from __future__ import annotations

from typing import Any

import pytest

from tests.support import (
    BUSINESS_DATE,
    build_archive_item_payload,
    build_archive_list_payload,
    build_cluster_detail_payload,
    build_daily_page_payload,
    build_page_article_link_rows,
    build_page_cluster_rows,
    build_page_index_rows,
    build_page_market_rows,
    build_page_snapshot_row,
    build_cluster_article_rows,
    build_cluster_row,
    build_processed_article_rows,
    load_module,
)


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
def app():
    main_module = load_module("app.main")
    app_instance = main_module.app
    if hasattr(app_instance, "dependency_overrides"):
        app_instance.dependency_overrides.clear()
    yield app_instance
    if hasattr(app_instance, "dependency_overrides"):
        app_instance.dependency_overrides.clear()


@pytest.fixture
def client(app):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client
