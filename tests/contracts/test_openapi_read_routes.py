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
    assert '/stock/api/pages/navigation' in paths
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
        ('/stock/api/pages/navigation', 'get'),
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


def test_openapi_documents_public_page_navigation_contract():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/navigation']['get']
    parameters = {
        parameter['name']: parameter
        for parameter in operation['parameters']
        if parameter['in'] == 'query'
    }

    assert parameters['businessDate'] == {
        'name': 'businessDate',
        'in': 'query',
        'required': True,
        'schema': {
            'type': 'string',
            'format': 'date',
            'title': 'Businessdate',
        },
    }

    response_schema = operation['responses']['200']['content']['application/json'][
        'schema'
    ]
    assert response_schema == {
        '$ref': '#/components/schemas/ApiSuccess_PageDateNavigationResponse_'
    }
    response_envelope = schema['components']['schemas'][
        'ApiSuccess_PageDateNavigationResponse_'
    ]
    assert response_envelope['properties']['data'] == {
        '$ref': '#/components/schemas/PageDateNavigationResponse'
    }

    response_schema = schema['components']['schemas']['PageDateNavigationResponse']
    assert response_schema['required'] == [
        'businessDate',
        'pageExists',
        'previousBusinessDate',
        'nextBusinessDate',
    ]
    assert response_schema['properties']['businessDate']['format'] == 'date'
    assert response_schema['properties']['previousBusinessDate']['anyOf'] == [
        {'type': 'string', 'format': 'date'},
        {'type': 'null'},
    ]
    assert response_schema['properties']['nextBusinessDate']['anyOf'] == [
        {'type': 'string', 'format': 'date'},
        {'type': 'null'},
    ]


def test_openapi_limits_archive_status_to_public_page_statuses():
    schema = app_module.app.openapi()
    operation = schema['paths']['/stock/api/pages/archive']['get']
    status_parameter = next(
        parameter
        for parameter in operation['parameters']
        if parameter['in'] == 'query' and parameter['name'] == 'status'
    )

    assert status_parameter['required'] is False
    assert status_parameter['schema']['anyOf'] == [
        {'$ref': '#/components/schemas/ArchiveStatus'},
        {'type': 'null'},
    ]
    assert schema['components']['schemas']['ArchiveStatus'] == {
        'type': 'string',
        'enum': ['READY', 'PARTIAL'],
    }
