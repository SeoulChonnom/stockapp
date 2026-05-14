from __future__ import annotations

import pytest

pytest.importorskip('fastapi')

from tests.support import load_module

app_module = load_module('app.main')


def test_openapi_includes_read_routes():
    app = app_module.app
    schema = app.openapi()
    paths = set(schema['paths'].keys())

    assert '/stock/api/pages/daily/latest' in paths
    assert '/stock/api/pages/daily' in paths
    assert '/stock/api/pages/archive' in paths
    assert '/stock/api/pages/{pageId}' in paths
    assert '/stock/api/news/clusters/{clusterId}' in paths
    assert '/stock/api/batch/market-daily' in paths
    assert '/stock/api/batch/jobs' in paths
    assert '/stock/api/batch/jobs/{jobId}' in paths
    assert '/stock/api/archive' not in paths


def test_openapi_read_route_methods_are_stable():
    app = app_module.app
    schema = app.openapi()

    expected_methods = {
        ('/stock/api/pages/daily/latest', 'get'),
        ('/stock/api/pages/daily', 'get'),
        ('/stock/api/pages/archive', 'get'),
        ('/stock/api/pages/{pageId}', 'get'),
        ('/stock/api/news/clusters/{clusterId}', 'get'),
        ('/stock/api/batch/market-daily', 'post'),
        ('/stock/api/batch/jobs', 'get'),
        ('/stock/api/batch/jobs/{jobId}', 'get'),
    }

    actual_methods = {
        (path, method)
        for path, methods in schema['paths'].items()
        for method in methods
        if path.startswith('/stock/api/')
    }

    assert expected_methods <= actual_methods
    assert ('/stock/api/archive', 'get') not in actual_methods
