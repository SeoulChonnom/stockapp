from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from tests.support import load_module

app_module = load_module("app.main")


def test_openapi_includes_read_routes():
    app = app_module.app
    schema = app.openapi()
    paths = set(schema["paths"].keys())

    assert "/stock/api/pages/daily/latest" in paths
    assert "/stock/api/pages/daily" in paths
    assert "/stock/api/pages/archive" in paths
    assert "/stock/api/pages/{pageId}" in paths
    assert "/stock/api/news/clusters/{clusterId}" in paths
    assert "/stock/api/batch/market-daily" in paths
    assert "/stock/api/batch/jobs" in paths
    assert "/stock/api/batch/jobs/{jobId}" in paths
