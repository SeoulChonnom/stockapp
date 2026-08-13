from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.core.exceptions import NotFoundError
from tests.support import jsonable, load_module

clusters_service_module = load_module('app.domains.clusters.service')

ClustersService = clusters_service_module.ClustersService


class FakeClusterRepository:
    def __init__(self, cluster_row, cluster_articles, processed_articles):
        self.cluster_row = cluster_row
        self.cluster_articles = cluster_articles
        self.processed_articles = processed_articles
        self.calls: list[tuple] = []

    async def get_cluster_by_uid(self, cluster_uid):
        self.calls.append(('get_cluster_by_uid', str(cluster_uid)))
        return (
            self.cluster_row
            if str(cluster_uid) == self.cluster_row['cluster_uid']
            else None
        )

    async def get_cluster_articles(self, cluster_id):
        self.calls.append(('get_cluster_articles', cluster_id))
        return self.cluster_articles

    async def get_processed_articles(self, article_ids):
        self.calls.append(('get_processed_articles', tuple(article_ids)))
        return [
            self.processed_articles[article_id]
            for article_id in article_ids
            if article_id in self.processed_articles
        ]


class FakeAiSummaryRepository:
    def __init__(self, summary):
        self.summary = summary
        self.calls: list[tuple] = []

    async def get_latest_cluster_summary(self, cluster_id, *, summary_type):
        self.calls.append((cluster_id, summary_type))
        return self.summary


def _summary_record(
    *,
    paragraphs,
    metadata,
    status='SUCCESS',
    fallback_used=False,
    error_message=None,
):
    from app.db.repositories.projections import AiSummaryRecord

    return AiSummaryRecord(
        summary_id=901,
        batch_job_id=801,
        summary_type='CLUSTER_DETAIL_ANALYSIS',
        business_date=datetime(2026, 3, 17, tzinfo=UTC).date(),
        market_type='US',
        cluster_id=7001,
        title='저장 분석',
        body='저장 분석 본문',
        paragraphs_json=paragraphs,
        model_name='test-model',
        prompt_version='test-prompt',
        status=status,
        fallback_used=fallback_used,
        error_message=error_message,
        metadata_json=metadata,
        generated_at=datetime(2026, 3, 18, 5, 0, tzinfo=UTC),
    )


@pytest.fixture
def cluster_repository(
    sample_cluster_row, sample_cluster_article_rows, sample_processed_article_rows
):
    processed_articles = {row['id']: row for row in sample_processed_article_rows}
    return FakeClusterRepository(
        sample_cluster_row, sample_cluster_article_rows, processed_articles
    )


@pytest.mark.anyio
async def test_cluster_service_returns_cluster_detail(
    cluster_repository, sample_cluster_detail_payload
):
    summary_repository = FakeAiSummaryRepository(None)
    service = ClustersService(cluster_repository, summary_repository)

    result = await service.get_cluster_detail(
        sample_cluster_detail_payload['clusterId']
    )
    assert isinstance(result, dict)
    payload = jsonable(result)

    assert payload['clusterId'] == sample_cluster_detail_payload['clusterId']
    assert payload['representativeArticle']['title'] == '엔비디아 급등에 반도체 강세'
    assert payload['representativeArticle']['sourceSummary'] == (
        '반도체 업종 강세가 나스닥 상승을 견인했다.'
    )
    assert payload['articles'][0]['title'] == '엔비디아 급등에 반도체 강세'
    assert payload['articles'][0]['sourceSummary'] == (
        '반도체 업종 강세가 나스닥 상승을 견인했다.'
    )
    assert payload['articles'][1]['title'] == '엔비디아 강세에 반도체 섹터 동반 상승'
    assert payload['articleCount'] == 6
    assert cluster_repository.calls[0][0] == 'get_cluster_by_uid'
    assert cluster_repository.calls[1][0] == 'get_cluster_articles'
    assert summary_repository.calls == [
        (7001, 'CLUSTER_DETAIL_ANALYSIS'),
    ]


@pytest.mark.anyio
async def test_cluster_service_reads_persisted_sections_and_summary_timestamp(
    cluster_repository,
):
    summary_repository = FakeAiSummaryRepository(
        _summary_record(
            paragraphs=[
                {
                    'kind': 'background',
                    'title': '발생 배경',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '반도체 업종 강세가 국내 시장으로 이어졌습니다.',
                                    'sourceArticleIds': [4001],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                }
                            ]
                        }
                    ],
                }
            ],
            metadata={
                'analysisStatus': 'READY',
                'analysisIssues': [],
                'conflictStatus': 'NONE',
            },
        )
    )
    service = ClustersService(cluster_repository, summary_repository)

    payload = jsonable(
        await service.get_cluster_detail('51f0d9a0-9fc5-4f15-a4f9-62856f128683')
    )

    assert payload['summary']['short'] == ('반도체 업종 강세가 나스닥 상승을 견인했다.')
    assert payload['summary']['long'] == (
        'PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.'
    )
    assert payload['summary']['analysisStatus'] == 'READY'
    assert payload['summary']['analysisGeneratedAt'] == '2026-03-18T05:00:00Z'
    assert payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0][
        'sourceArticleIds'
    ] == [4001]
    assert payload['lastUpdatedAt'] == '2026-03-18T06:20:00Z'


@pytest.mark.anyio
async def test_cluster_service_surfaces_latest_failed_summary_as_unavailable(
    cluster_repository,
):
    summary_repository = FakeAiSummaryRepository(
        _summary_record(
            paragraphs=[
                {
                    'kind': 'background',
                    'title': '발생 배경',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '오래된 성공 분석이 남아 있습니다.',
                                    'sourceArticleIds': [4001],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                }
                            ]
                        }
                    ],
                }
            ],
            metadata={
                'analysisStatus': 'UNAVAILABLE',
                'analysisIssues': [
                    {
                        'code': 'ANALYSIS_GENERATION_FAILED',
                        'message': 'provider secret: quota exhausted',
                    }
                ],
                'conflictStatus': 'NOT_CHECKED',
            },
            status='FALLBACK',
            fallback_used=True,
            error_message='provider secret: quota exhausted',
        )
    )
    service = ClustersService(cluster_repository, summary_repository)

    payload = jsonable(
        await service.get_cluster_detail('51f0d9a0-9fc5-4f15-a4f9-62856f128683')
    )

    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisGeneratedAt'] is None
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }
    ]
    assert payload['summary']['sections'] == []


@pytest.mark.anyio
async def test_cluster_service_keeps_representative_missing_as_not_found(
    cluster_repository,
):
    cluster_repository.processed_articles.pop(4001)
    service = ClustersService(
        cluster_repository,
        FakeAiSummaryRepository(None),
    )

    with pytest.raises(NotFoundError) as exc_info:
        await service.get_cluster_detail('51f0d9a0-9fc5-4f15-a4f9-62856f128683')

    assert exc_info.value.code == 'CLUSTER_REPRESENTATIVE_ARTICLE_NOT_FOUND'
