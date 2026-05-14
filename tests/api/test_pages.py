from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError
from tests.support import build_test_bearer_headers, load_module

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

pages_router_module = load_module('app.domains.pages.router')
archive_router_module = load_module('app.domains.archive.router')


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
        return self.page_payload

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
def client(app, sample_daily_page_payload, sample_archive_list_payload):
    fake_pages_service = FakePagesService(sample_daily_page_payload)
    fake_archive_service = FakeArchiveService(sample_archive_list_payload)
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
        'markets',
        'metadata',
    } <= set(data)
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


def test_get_daily_page_requires_business_date(client):
    response = client.get(
        '/stock/api/pages/daily', headers=build_test_bearer_headers('USER')
    )

    assert response.status_code == 422


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


def test_get_page_by_id_returns_404_when_missing(client):
    response = client.get(
        '/stock/api/pages/999', headers=build_test_bearer_headers('USER')
    )

    assert response.status_code == 404
