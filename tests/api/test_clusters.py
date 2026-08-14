from __future__ import annotations

from datetime import UTC, datetime

import pytest  # pyright: ignore[reportMissingImports]

from app.core.exceptions import NotFoundError
from tests.support import load_module

pytest.importorskip('fastapi')
from fastapi import FastAPI  # pyright: ignore[reportMissingImports]
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

clusters_router_module = load_module('app.domains.clusters.router')
clusters_service_module = load_module('app.domains.clusters.service')
auth_module = load_module('app.api.deps.auth')
exceptions_module = load_module('app.core.exceptions')


def _persisted_grouping(status: str):
    from app.db.repositories.projections import (
        ArticleGroupingRecord,
        ArticleGroupMemberRecord,
        ArticleGroupRecord,
    )

    if status == 'READY':
        groups = (
            ArticleGroupRecord(
                similar_group_id=9101,
                cluster_id=7001,
                group_rank=1,
                representative_article_id=4001,
                algorithm_version='v1',
                generated_at=datetime(2026, 3, 18, 5, 0, tzinfo=UTC),
                members=(
                    ArticleGroupMemberRecord(9101, 4001, 1.0, 2, True, 1),
                    ArticleGroupMemberRecord(9101, 4002, 0.8, 1, False, 2),
                ),
            ),
            ArticleGroupRecord(
                similar_group_id=9102,
                cluster_id=7001,
                group_rank=2,
                representative_article_id=4003,
                algorithm_version='v1',
                generated_at=datetime(2026, 3, 18, 5, 0, tzinfo=UTC),
                members=(ArticleGroupMemberRecord(9102, 4003, 1.0, 0, True, 1),),
            ),
        )
        generated_at = datetime(2026, 3, 18, 5, 0, tzinfo=UTC)
        issue_code = None
    else:
        groups = tuple(
            ArticleGroupRecord(
                similar_group_id=9200 + index,
                cluster_id=7001,
                group_rank=index,
                representative_article_id=4000 + index,
                algorithm_version='v1',
                generated_at=datetime(2026, 3, 18, 5, 0, tzinfo=UTC),
                members=(
                    ArticleGroupMemberRecord(
                        9200 + index,
                        4000 + index,
                        1.0,
                        count,
                        True,
                        1,
                    ),
                ),
            )
            for index, count in enumerate((4, 2, 1), start=1)
        )
        generated_at = None
        issue_code = 'SIMILARITY_GROUPING_FAILED'
    return ArticleGroupingRecord(
        status=status,
        generated_at=generated_at,
        issue_code=issue_code,
        algorithm_version='v1',
        groups=groups,
        members=tuple(member for group in groups for member in group.members),
    )


class PersistedClusterRepository:
    def __init__(self, cluster, cluster_articles, processed_articles, grouping):
        self.cluster = cluster
        self.cluster_articles = cluster_articles
        self.processed_articles = {row['id']: row for row in processed_articles}
        self.grouping = grouping

    async def get_cluster_by_uid(self, cluster_uid):
        return self.cluster if str(cluster_uid) == self.cluster['cluster_uid'] else None

    async def get_cluster_articles(self, cluster_id):
        return self.cluster_articles

    async def get_processed_articles(self, article_ids):
        return [
            self.processed_articles[article_id]
            for article_id in article_ids
            if article_id in self.processed_articles
        ]

    async def get_cluster_grouping(self, cluster_id):
        return self.grouping


def _build_real_cluster_app(
    sample_cluster_row,
    sample_cluster_article_rows,
    sample_processed_article_rows,
    grouping,
):
    service = clusters_service_module.ClustersService(
        PersistedClusterRepository(
            sample_cluster_row,
            sample_cluster_article_rows,
            sample_processed_article_rows,
            grouping,
        )
    )
    app = FastAPI()
    exceptions_module.register_exception_handlers(app)
    app.include_router(clusters_router_module.router, prefix='/stock/api')
    app.dependency_overrides[auth_module.get_current_user] = lambda: (
        auth_module.CurrentUser(user_id='test-user', roles=('USER',))
    )
    app.dependency_overrides[clusters_router_module.get_clusters_service] = lambda: (
        service
    )
    return app


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


def test_get_cluster_detail_real_service_returns_persisted_ready_contract(
    sample_cluster_row,
    sample_cluster_article_rows,
    sample_processed_article_rows,
):
    app = _build_real_cluster_app(
        sample_cluster_row,
        sample_cluster_article_rows,
        sample_processed_article_rows,
        _persisted_grouping('READY'),
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                f'/stock/api/news/clusters/{sample_cluster_row["cluster_uid"]}'
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()['data']
    required_article_fields = {
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
    }
    assert required_article_fields <= set(payload['representativeArticle'])
    assert all(
        required_article_fields <= set(article) for article in payload['articles']
    )
    assert payload['articleGrouping']['status'] == 'READY'
    assert payload['articleGrouping']['generatedAt'] is not None
    assert payload['articleGrouping']['issue'] is None
    assert [article['similarGroupId'] for article in payload['articles']] == [
        f'sim-{sample_cluster_row["cluster_uid"]}-1',
        f'sim-{sample_cluster_row["cluster_uid"]}-1',
        f'sim-{sample_cluster_row["cluster_uid"]}-2',
    ]
    assert [article['exactDuplicateCount'] for article in payload['articles']] == [
        2,
        1,
        0,
    ]
    assert all(
        '910' not in article['similarGroupId'] for article in payload['articles']
    )


def test_get_cluster_detail_real_service_returns_persisted_unavailable_contract(
    sample_cluster_row,
    sample_cluster_article_rows,
    sample_processed_article_rows,
):
    app = _build_real_cluster_app(
        sample_cluster_row,
        sample_cluster_article_rows,
        sample_processed_article_rows,
        _persisted_grouping('UNAVAILABLE'),
    )
    try:
        with TestClient(app) as test_client:
            response = test_client.get(
                f'/stock/api/news/clusters/{sample_cluster_row["cluster_uid"]}'
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()['data']
    assert payload['articleGrouping'] == {
        'status': 'UNAVAILABLE',
        'generatedAt': None,
        'issue': {
            'code': 'SIMILARITY_GROUPING_FAILED',
            'message': '유사 기사 묶음을 생성하지 못했습니다.',
        },
    }
    assert [article['similarGroupId'] for article in payload['articles']] == [
        f'sim-{sample_cluster_row["cluster_uid"]}-1',
        f'sim-{sample_cluster_row["cluster_uid"]}-2',
        f'sim-{sample_cluster_row["cluster_uid"]}-3',
    ]
    assert [article['exactDuplicateCount'] for article in payload['articles']] == [
        4,
        2,
        1,
    ]


@pytest.mark.parametrize('grouping', [None, {'status': 'READY', 'groups': ()}])
def test_get_cluster_detail_real_service_sanitizes_grouping_integrity_failure(
    grouping,
    sample_cluster_row,
    sample_cluster_article_rows,
    sample_processed_article_rows,
):
    app = _build_real_cluster_app(
        sample_cluster_row,
        sample_cluster_article_rows,
        sample_processed_article_rows,
        grouping,
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.get(
                f'/stock/api/news/clusters/{sample_cluster_row["cluster_uid"]}'
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()['error'] == {
        'code': 'INTERNAL_SERVER_ERROR',
        'message': 'Internal server error',
    }
    assert 'cluster article grouping' not in response.text


@pytest.mark.parametrize('issue_code', [None, 'BAD_ISSUE'])
def test_get_cluster_detail_real_service_sanitizes_unavailable_issue_integrity_failure(
    issue_code,
    sample_cluster_row,
    sample_cluster_article_rows,
    sample_processed_article_rows,
):
    from dataclasses import replace

    app = _build_real_cluster_app(
        sample_cluster_row,
        sample_cluster_article_rows,
        sample_processed_article_rows,
        replace(_persisted_grouping('UNAVAILABLE'), issue_code=issue_code),
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as test_client:
            response = test_client.get(
                f'/stock/api/news/clusters/{sample_cluster_row["cluster_uid"]}'
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert response.json()['error'] == {
        'code': 'INTERNAL_SERVER_ERROR',
        'message': 'Internal server error',
    }
    assert 'BAD_ISSUE' not in response.text


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
