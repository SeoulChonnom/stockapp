from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError
from tests.support import load_module

pytest.importorskip('fastapi')
from fastapi import FastAPI  # pyright: ignore[reportMissingImports]
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

clusters_router_module = load_module('app.domains.clusters.router')
auth_module = load_module('app.api.deps.auth')
exceptions_module = load_module('app.core.exceptions')


class FakeClustersService:
    def __init__(self, payload: dict):
        self.payload = {
            **payload,
            'articleCount': 3,
            'representativeArticle': {
                **payload['representativeArticle'],
                'sourceSummary': '반도체 업종 강세가 나스닥 상승을 견인했다.',
            },
            'articles': [
                {**article, 'sourceSummary': f'요약 {index}'}
                for index, article in enumerate(payload['articles'], start=1)
            ],
        }

    async def get_cluster_detail(self, cluster_id):
        if str(cluster_id) == self.payload['clusterId']:
            return self.payload
        raise NotFoundError(
            'CLUSTER_NOT_FOUND', '요청한 뉴스 클러스터를 찾을 수 없습니다.'
        )


@pytest.fixture
def client(sample_cluster_detail_payload):
    fake_clusters_service = FakeClustersService(sample_cluster_detail_payload)
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(clusters_router_module.router, prefix='/stock/api')
    app.dependency_overrides[auth_module.get_current_user] = lambda: (
        auth_module.CurrentUser(
            user_id='test-user',
            roles=('USER',),
        )
    )
    app.dependency_overrides[clusters_router_module.get_clusters_service] = lambda: (
        fake_clusters_service
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_get_cluster_detail_returns_contract(client, sample_cluster_detail_payload):
    response = client.get(
        f'/stock/api/news/clusters/{sample_cluster_detail_payload["clusterId"]}'
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert {
        'clusterId',
        'businessDate',
        'marketType',
        'marketLabel',
        'title',
        'tags',
        'summary',
        'representativeArticle',
        'articles',
        'articleGrouping',
        'lastUpdatedAt',
        'articleCount',
    } <= set(payload)
    assert 'analysis' not in payload['summary']
    assert {
        'short',
        'long',
        'analysisStatus',
        'analysisGeneratedAt',
        'analysisIssues',
        'conflictStatus',
        'sections',
    } <= set(payload['summary'])
    assert {
        'processedArticleId',
        'title',
        'publisherName',
        'publishedAt',
        'originLink',
        'naverLink',
        'sourceSummary',
        'similarGroupId',
        'isSimilarGroupRepresentative',
        'exactDuplicateCount',
    } <= set(payload['representativeArticle'])
    assert payload['clusterId'] == sample_cluster_detail_payload['clusterId']
    assert payload['marketType'] == 'US'
    assert payload['representativeArticle']['publisherName'] == '매일경제'
    assert payload['representativeArticle']['sourceSummary'] == (
        '반도체 업종 강세가 나스닥 상승을 견인했다.'
    )
    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisGeneratedAt'] is None
    assert payload['summary']['conflictStatus'] == 'NOT_CHECKED'
    assert payload['summary']['sections'] == []
    assert payload['articleGrouping'] == {
        'status': 'UNAVAILABLE',
        'generatedAt': None,
        'issue': {
            'code': 'SIMILARITY_GROUPING_FAILED',
            'message': '유사 기사 묶음을 생성하지 못했습니다.',
        },
    }
    assert payload['articleCount'] == 3
    assert payload['articles'][1]['title'] == '엔비디아 강세에 반도체 섹터 동반 상승'
    assert [article['similarGroupId'] for article in payload['articles']] == [
        f'sim-{payload["clusterId"]}-1',
        f'sim-{payload["clusterId"]}-2',
        f'sim-{payload["clusterId"]}-3',
    ]
    assert all(
        article['isSimilarGroupRepresentative'] for article in payload['articles']
    )
    assert all(article['exactDuplicateCount'] == 0 for article in payload['articles'])


def test_get_cluster_detail_rejects_malformed_uuid(client):
    response = client.get('/stock/api/news/clusters/not-a-uuid')

    assert response.status_code == 422


def test_get_cluster_detail_returns_404_when_missing(client):
    response = client.get(
        '/stock/api/news/clusters/7b9845f6-5c3d-4f2c-a81d-8dcb0b5dd6d2'
    )

    assert response.status_code == 404


def test_get_clusters_service_injects_ai_summary_repository(monkeypatch):
    created = {}

    class FakeClusterRepository:
        def __init__(self, session):
            created['cluster_session'] = session

    class FakeAiSummaryRepository:
        def __init__(self, session):
            created['summary_session'] = session

    monkeypatch.setattr(
        clusters_router_module,
        'ClusterRepository',
        FakeClusterRepository,
    )
    monkeypatch.setattr(
        clusters_router_module,
        'AiSummaryRepository',
        FakeAiSummaryRepository,
    )

    session = object()
    service = clusters_router_module.get_clusters_service(session)

    assert created == {
        'cluster_session': session,
        'summary_session': session,
    }
    assert isinstance(service._repo, FakeClusterRepository)
    assert isinstance(service._ai_summary_repo, FakeAiSummaryRepository)
