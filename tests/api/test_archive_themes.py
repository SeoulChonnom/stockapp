from __future__ import annotations

import pytest

from tests.support import build_test_bearer_headers, load_module

pytest.importorskip('fastapi')
from fastapi import FastAPI  # pyright: ignore[reportMissingImports]
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

archive_router_module = load_module('app.domains.archive.router')
exceptions_module = load_module('app.core.exceptions')
assembler_module = load_module('app.domains.archive.assembler')
ThemeCatalogError = assembler_module.ThemeCatalogError


class FakeArchiveService:
    def __init__(self, theme_catalog: list[dict[str, object]]) -> None:
        self.theme_catalog = theme_catalog

    async def list_archive(self, **_kwargs):
        return {'items': [], 'pagination': {'page': 1, 'size': 30, 'totalCount': 0}}

    async def list_theme_catalog(self):
        return self.theme_catalog


@pytest.fixture
def client():
    theme_catalog = [
        {
            'code': 'ROOT_B',
            'label': 'Root B',
            'description': 'Root B description',
            'children': [],
        },
        {
            'code': 'ROOT_A',
            'label': 'Root A',
            'description': 'Root A description',
            'children': [
                {
                    'code': 'ROOT_A_CHILD_B',
                    'label': 'Child B',
                    'description': 'Child B description',
                    'children': [],
                },
                {
                    'code': 'ROOT_A_CHILD_A',
                    'label': 'Child A',
                    'description': 'Child A description',
                    'children': [],
                },
            ],
        },
    ]
    fake_service = FakeArchiveService(theme_catalog)
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(archive_router_module.router, prefix='/stock/api')
    app.dependency_overrides[archive_router_module.get_archive_service] = lambda: (
        fake_service
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_archive_theme_catalog_returns_authenticated_success_envelope(client):
    response = client.get(
        '/stock/api/pages/archive/themes',
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {'success', 'data', 'meta'}
    assert payload['success'] is True
    assert payload['data'][0]['code'] == 'ROOT_B'
    assert payload['data'][1]['code'] == 'ROOT_A'
    assert [child['code'] for child in payload['data'][1]['children']] == [
        'ROOT_A_CHILD_B',
        'ROOT_A_CHILD_A',
    ]
    assert payload['data'][0]['children'] == []
    assert payload['meta']['requestId']
    assert payload['meta']['timestamp']


def test_archive_theme_catalog_requires_archive_read_authentication(client):
    response = client.get('/stock/api/pages/archive/themes')

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_MISSING_BEARER_TOKEN'


def test_archive_theme_catalog_openapi_has_strict_recursive_schema(client):
    schema = client.app.openapi()
    paths = list(schema['paths'])
    assert paths.index('/stock/api/pages/archive/themes') < paths.index(
        '/stock/api/pages/archive'
    )

    operation = schema['paths']['/stock/api/pages/archive/themes']['get']
    assert operation['responses']['200']['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/ApiSuccess_list_ThemeNodeResponse__'
    }
    theme_node = schema['components']['schemas']['ThemeNodeResponse']
    assert theme_node['required'] == ['code', 'label', 'description', 'children']
    assert theme_node['additionalProperties'] is False
    assert theme_node['properties']['children'] == {
        'items': {'$ref': '#/components/schemas/ThemeNodeResponse'},
        'type': 'array',
        'title': 'Children',
    }


def test_archive_theme_catalog_hides_internal_catalog_failure_details():
    class FailingArchiveService(FakeArchiveService):
        async def list_theme_catalog(self):
            raise ThemeCatalogError('cycle detected at SECRET_INTERNAL_CODE')

    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(archive_router_module.router, prefix='/stock/api')
    app.dependency_overrides[archive_router_module.get_archive_service] = lambda: (
        FailingArchiveService([])
    )

    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            '/stock/api/pages/archive/themes',
            headers=build_test_bearer_headers('USER'),
        )

    app.dependency_overrides.clear()
    assert response.status_code == 500
    assert response.json()['error'] == {
        'code': 'INTERNAL_SERVER_ERROR',
        'message': 'Internal server error',
    }
