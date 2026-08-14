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
pages_service_module = load_module('app.domains.pages.service')
llm_provider_module = load_module('app.batch.providers.llm_provider')

KEY_POINTS = [
    {
        'kind': 'direction',
        'label': '시장 방향',
        'text': '주요 지수가 상승했습니다.',
        'direction': 'UP',
    },
    {
        'kind': 'driver',
        'label': '주요 원인',
        'text': '반도체 강세가 상승을 이끌었습니다.',
    },
    {
        'kind': 'watch',
        'label': '관전 포인트',
        'text': '다음 물가 지표를 확인해야 합니다.',
    },
]


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

    async def get_date_navigation(self, business_date):
        page_exists = str(business_date) == self.page_payload['businessDate']
        return {
            'businessDate': business_date,
            'pageExists': page_exists,
            'previousBusinessDate': None,
            'nextBusinessDate': None,
        }


class FakeArchiveService:
    def __init__(self, archive_payload: dict):
        self.archive_payload = archive_payload
        self.list_kwargs = None

    async def list_archive(self, **kwargs):
        self.list_kwargs = kwargs
        return self.archive_payload


class PersistedPageRepository:
    def __init__(
        self,
        *,
        page,
        markets,
        indices,
        clusters,
        article_links,
        neighbors,
        versions,
    ):
        self.page = page
        self.markets = markets
        self.indices = indices
        self.clusters = clusters
        self.article_links = article_links
        self.neighbors = neighbors
        self.versions = versions

    async def get_latest_public_page_header(self):
        return self.page

    async def get_page_markets(self, page_id):
        assert page_id == self.page['id']
        return self.markets

    async def get_page_indices(self, page_market_ids):
        assert page_market_ids == [market['id'] for market in self.markets]
        return self.indices

    async def get_page_clusters(self, page_market_ids):
        assert page_market_ids == [market['id'] for market in self.markets]
        return self.clusters

    async def get_page_article_links(self, page_market_ids):
        assert page_market_ids == [market['id'] for market in self.markets]
        return self.article_links

    async def get_adjacent_public_business_dates(self, business_date):
        assert business_date == self.page['business_date']
        return self.neighbors

    async def list_page_versions(self, business_date):
        assert business_date == self.page['business_date']
        return self.versions


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
        test_client.archive_service = fake_archive_service
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
        'keyPoints',
        'markets',
        'metadata',
    } <= set(data)
    assert data['issues'] == []
    assert data['keyPoints'] == []
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


def test_repeated_api_reads_return_same_persisted_key_points_without_provider(
    monkeypatch,
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    provider_calls = []

    def fail_provider_init(*args, **kwargs):
        provider_calls.append((args, kwargs))
        raise AssertionError('page reads must not construct an AI provider')

    monkeypatch.setattr(
        llm_provider_module.BatchLlmProvider,
        '__init__',
        fail_provider_init,
    )
    metadata = {'keyPoints': KEY_POINTS}
    repository = PersistedPageRepository(
        page={
            **sample_page_snapshot_row,
            'metadata_json': metadata,
        },
        markets=sample_page_market_rows,
        indices=sample_page_index_rows,
        clusters=sample_page_cluster_rows,
        article_links=sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )
    service = pages_service_module.PagesService(repository)
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(pages_router_module.router, prefix='/stock/api')
    app.dependency_overrides[pages_router_module.get_pages_service] = lambda: service

    with TestClient(app) as test_client:
        first = test_client.get(
            '/stock/api/pages/daily/latest',
            headers=build_test_bearer_headers('USER'),
        )
        second = test_client.get(
            '/stock/api/pages/daily/latest',
            headers=build_test_bearer_headers('USER'),
        )

    app.dependency_overrides.clear()
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()['data']['keyPoints'] == KEY_POINTS
    assert second.json()['data']['keyPoints'] == KEY_POINTS
    assert metadata == {'keyPoints': KEY_POINTS}
    assert provider_calls == []


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


def test_get_page_date_navigation_returns_required_contract_keys(client):
    response = client.get(
        '/stock/api/pages/navigation',
        params={'businessDate': '2026-08-13'},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    assert set(response.json()['data']) == {
        'businessDate',
        'pageExists',
        'previousBusinessDate',
        'nextBusinessDate',
    }


def test_get_page_date_navigation_returns_200_for_missing_date(client):
    response = client.get(
        '/stock/api/pages/navigation',
        params={'businessDate': '2026-08-13'},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 200
    assert response.json()['data']['pageExists'] is False


def test_get_page_date_navigation_rejects_malformed_date(client):
    response = client.get(
        '/stock/api/pages/navigation',
        params={'businessDate': 'not-a-date'},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 422


@pytest.mark.parametrize('status', ['archived', 'FAILED'])
def test_get_archive_rejects_non_public_status_with_standard_422(client, status):
    response = client.get(
        '/stock/api/pages/archive',
        params={'status': status},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload['success'] is False
    assert payload['error']['code'] == 'REQUEST_VALIDATION_ERROR'
    assert payload['meta']['requestId']


def test_archive_openapi_documents_literal_status_validation_as_422(client):
    responses = client.get('/openapi.json').json()['paths']['/stock/api/pages/archive'][
        'get'
    ]['responses']

    assert '400' not in responses
    assert '422' in responses
    assert 'REQUEST_VALIDATION_ERROR' in responses['422']['description']


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


def test_get_archive_passes_repeated_theme_market_and_query_filters(client):
    response = client.get(
        '/stock/api/pages/archive',
        params=[
            ('theme', 'ROOT_A'),
            ('theme', 'ROOT_B'),
            ('marketType', 'KR'),
            ('q', 'NVIDIA earnings'),
        ],
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 200
    assert client.archive_service.list_kwargs == {
        'from_date': None,
        'to_date': None,
        'status': None,
        'market_type': 'KR',
        'themes': ['ROOT_A', 'ROOT_B'],
        'query': 'NVIDIA earnings',
        'page': 1,
        'size': 30,
    }


def test_get_archive_rejects_more_than_ten_themes(client):
    response = client.get(
        '/stock/api/pages/archive',
        params=[('theme', f'THEME_{index}') for index in range(11)],
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'REQUEST_VALIDATION_ERROR'


def test_get_archive_rejects_query_shorter_than_two_characters(client):
    response = client.get(
        '/stock/api/pages/archive',
        params={'q': 'a'},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 422
    assert response.json()['error']['code'] == 'REQUEST_VALIDATION_ERROR'


def test_archive_openapi_documents_correlated_filter_errors(client):
    responses = client.get('/openapi.json').json()['paths']['/stock/api/pages/archive'][
        'get'
    ]['responses']

    assert 'INVALID_THEME' in responses['422']['description']


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
