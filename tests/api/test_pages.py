from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError
from tests.support import build_test_bearer_headers, load_module

pytest.importorskip('fastapi')
from fastapi import FastAPI  # pyright: ignore[reportMissingImports]
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

pages_router_module = load_module('app.domains.pages.router')
archive_router_module = load_module('app.domains.archive.router')
exceptions_module = load_module('app.core.exceptions')


class FakePagesService:
    def __init__(self, page_payload: dict, missing_page_ids: set[int] | None = None):
        self.page_payload = page_payload
        self.missing_page_ids = missing_page_ids or {999}

    async def get_latest_page(self):
        return self.page_payload

    async def get_page_by_date(self, business_date, version_no=None):
        if str(business_date) != self.page_payload['businessDate']:
            raise NotFoundError(
                'PAGE_NOT_FOUND', '요청한 날짜의 페이지가 존재하지 않습니다.'
            )
        if version_no == 1:
            historical_payload = dict(self.page_payload)
            historical_payload['metadata'] = {
                **self.page_payload['metadata'],
                'isLatest': False,
            }
            return historical_payload
        if version_no == 999:
            raise NotFoundError(
                'PAGE_VERSION_NOT_FOUND', '요청한 페이지 버전이 존재하지 않습니다.'
            )
        latest_payload = dict(self.page_payload)
        latest_payload['metadata'] = {**self.page_payload['metadata'], 'isLatest': True}
        return latest_payload

    async def get_page_by_id(self, page_id):
        if page_id in self.missing_page_ids:
            raise NotFoundError('PAGE_NOT_FOUND', '요청한 페이지를 찾을 수 없습니다.')
        if page_id == self.page_payload['pageId']:
            return self.page_payload
        raise NotFoundError('PAGE_NOT_FOUND', '요청한 페이지를 찾을 수 없습니다.')


class FakeArchiveService:
    def __init__(self, archive_payload: dict):
        self.archive_payload = archive_payload

    async def list_archive(self, **_kwargs):
        return self.archive_payload


@pytest.fixture
def client(sample_daily_page_payload, sample_archive_list_payload):
    fake_pages_service = FakePagesService(sample_daily_page_payload)
    fake_archive_service = FakeArchiveService(sample_archive_list_payload)
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(archive_router_module.router, prefix='/stock/api')
    app.include_router(pages_router_module.router, prefix='/stock/api')
    app.dependency_overrides[pages_router_module.get_pages_service] = lambda: (
        fake_pages_service
    )
    app.dependency_overrides[archive_router_module.get_archive_service] = lambda: (
        fake_archive_service
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.mark.parametrize('role', ['USER', 'ADMIN'], ids=['user', 'admin'])
def test_get_latest_page_allows_user_and_admin_roles(
    client, sample_daily_page_payload, role
):
    response = client.get(
        '/stock/api/pages/daily/latest', headers=build_test_bearer_headers(role)
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {'success', 'data', 'meta'}
    assert payload['success'] is True
    data = payload['data']
    assert {
        'pageId',
        'businessDate',
        'versionNo',
        'pageTitle',
        'status',
        'globalHeadline',
        'generatedAt',
        'partialMessage',
        'issues',
        'markets',
        'metadata',
    } <= set(data)
    assert data['issues'] == []
    assert data['pageId'] == sample_daily_page_payload['pageId']
    assert data['markets'][0]['marketType'] == 'US'
    assert (
        data['markets'][0]['topClusters'][0]['representativeArticle']['originLink']
        == 'https://example.com/article1'
    )
    assert len(data['markets'][0]['articleLinks']) == 2
    assert (
        data['markets'][0]['articleLinks'][1]['originLink']
        == 'https://example.com/article2'
    )
    assert data['markets'][1]['indices'][0]['indexCode'] == 'KS11'
    assert 'articleLinks' not in data
    assert payload['meta']['requestId']
    assert payload['meta']['timestamp']
    assert data['generatedAt'].endswith('Z')
    assert data['metadata']['lastUpdatedAt'].endswith('Z')


def test_get_latest_page_rejects_missing_token_as_unauthorized(client):
    response = client.get('/stock/api/pages/daily/latest')

    assert response.status_code == 401


def test_get_latest_page_rejects_invalid_token_as_unauthorized(client):
    response = client.get(
        '/stock/api/pages/daily/latest',
        headers={'Authorization': 'Bearer definitely-not-a-valid-test-token'},
    )

    assert response.status_code == 401


def test_get_daily_page_uses_business_date_query(client, sample_daily_page_payload):
    response = client.get(
        '/stock/api/pages/daily',
        params={'businessDate': sample_daily_page_payload['businessDate']},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    data = response.json()['data']
    assert data['businessDate'] == sample_daily_page_payload['businessDate']
    assert data['versionNo'] == sample_daily_page_payload['versionNo']
    assert data['metadata']['isLatest'] is True


def test_get_daily_page_distinguishes_missing_version(
    client, sample_daily_page_payload
):
    response = client.get(
        '/stock/api/pages/daily',
        params={
            'businessDate': sample_daily_page_payload['businessDate'],
            'versionNo': 999,
        },
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 404
    assert response.json()['error']['code'] == 'PAGE_VERSION_NOT_FOUND'


def test_get_historical_ready_page_sets_conservative_cache_headers(
    client, sample_daily_page_payload
):
    response = client.get(
        '/stock/api/pages/daily',
        params={
            'businessDate': sample_daily_page_payload['businessDate'],
            'versionNo': 1,
        },
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    assert response.json()['data']['metadata']['isLatest'] is False
    assert response.headers['cache-control'] == 'public, max-age=300, immutable'


def test_get_daily_page_requires_business_date(client):
    response = client.get(
        '/stock/api/pages/daily', headers=build_test_bearer_headers('USER')
    )

    assert response.status_code == 422


def test_get_archive_rejects_invalid_status_with_standard_422(client):
    response = client.get(
        '/stock/api/pages/archive',
        params={'status': 'archived'},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'REQUEST_VALIDATION_ERROR'
    assert payload['meta']['requestId']


def test_unexpected_page_failure_uses_standard_500_envelope(
    sample_archive_list_payload,
):
    class BrokenPagesService:
        async def get_latest_page(self):
            raise RuntimeError('boom')

    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(archive_router_module.router, prefix='/stock/api')
    app.include_router(pages_router_module.router, prefix='/stock/api')
    app.dependency_overrides[pages_router_module.get_pages_service] = lambda: (
        BrokenPagesService()
    )
    app.dependency_overrides[archive_router_module.get_archive_service] = lambda: (
        FakeArchiveService(sample_archive_list_payload)
    )
    with TestClient(app, raise_server_exceptions=False) as test_client:
        response = test_client.get(
            '/stock/api/pages/daily/latest', headers=build_test_bearer_headers('USER')
        )

    app.dependency_overrides.clear()
    assert response.status_code == 500
    payload = response.json()
    assert payload['success'] is False
    assert payload['error'] == {
        'code': 'INTERNAL_SERVER_ERROR',
        'message': 'Internal server error',
    }
    assert payload['meta']['requestId']


def test_get_archive_lists_latest_snapshot_per_date(
    client, sample_archive_list_payload
):
    response = client.get(
        '/stock/api/pages/archive',
        params={
            'fromDate': '2026-03-16',
            'toDate': '2026-03-17',
            'status': 'READY',
            'page': 1,
            'size': 30,
        },
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert set(payload) == {'items', 'pagination'}
    assert {
        'pageId',
        'businessDate',
        'pageTitle',
        'headlineSummary',
        'status',
        'generatedAt',
        'partialMessage',
    } <= set(payload['items'][0])
    assert (
        payload['items'][0]['businessDate']
        == sample_archive_list_payload['items'][0]['businessDate']
    )
    assert payload['pagination']['totalCount'] == 2


def test_daily_page_exposes_existing_neighbor_dates_not_calendar_arithmetic(
    client, sample_daily_page_payload
):
    response = client.get(
        '/stock/api/pages/daily',
        params={'businessDate': sample_daily_page_payload['businessDate']},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    navigation = response.json()['data']['navigation']
    assert set(navigation) == {'previousBusinessDate', 'nextBusinessDate'}
    # 2026-03-17 minus one calendar day is 2026-03-16, which has no page.
    assert navigation['previousBusinessDate'] == '2026-03-13'
    assert navigation['nextBusinessDate'] is None


def test_latest_page_always_carries_navigation_with_no_next_date(client):
    response = client.get(
        '/stock/api/pages/daily/latest', headers=build_test_bearer_headers('USER')
    )

    assert response.status_code == 200
    data = response.json()['data']
    assert 'navigation' in data
    assert data['navigation']['nextBusinessDate'] is None


def test_daily_page_exposes_version_picker_entries(client, sample_daily_page_payload):
    response = client.get(
        '/stock/api/pages/daily',
        params={'businessDate': sample_daily_page_payload['businessDate']},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    versions = response.json()['data']['versions']
    assert len(versions) == 3
    assert [version['versionNo'] for version in versions] == [3, 2, 1]
    assert set(versions[0]) == {
        'pageId',
        'versionNo',
        'status',
        'generatedAt',
        'isLatest',
    }
    assert versions[0]['isLatest'] is True
    assert [version['isLatest'] for version in versions[1:]] == [False, False]
    assert versions[0]['generatedAt'].endswith('Z')


def test_page_by_id_carries_navigation_and_versions(client, sample_daily_page_payload):
    response = client.get(
        f'/stock/api/pages/{sample_daily_page_payload["pageId"]}',
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    data = response.json()['data']
    assert data['navigation']['previousBusinessDate'] == '2026-03-13'
    assert [version['versionNo'] for version in data['versions']] == [3, 2, 1]


def test_get_page_by_id_returns_404_when_missing(client):
    response = client.get(
        '/stock/api/pages/999', headers=build_test_bearer_headers('USER')
    )

    assert response.status_code == 404
