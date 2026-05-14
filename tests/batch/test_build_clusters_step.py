from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.support import BUSINESS_DATE, RecordingAsyncSession, load_module

build_clusters_module = load_module('app.batch.steps.build_clusters')
projections_module = load_module('app.db.repositories.projections')

BuildClustersStep = build_clusters_module.BuildClustersStep
BatchExecutionContext = load_module('app.batch.models').BatchExecutionContext


@dataclass
class FakeBatchRepository:
    session: RecordingAsyncSession
    events: list[dict]

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        self.events.append({'step_code': step_code, 'message': message, **kwargs})


class FakeProcessedRepo:
    def __init__(self, session):
        _ = session

    async def list_by_business_date(self, business_date, *, market_type=None):
        _ = (business_date, market_type)
        return [
            projections_module.NewsArticleProcessedRecord(
                processed_article_id=4001,
                business_date=BUSINESS_DATE,
                market_type='US',
                dedupe_hash='a' * 64,
                canonical_title='엔비디아 급등에 반도체 강세',
                publisher_name='매일경제',
                published_at=None,
                origin_link='https://example.com/article1',
                naver_link='https://search.naver.com/article1',
                source_summary='반도체 업종 강세가 나스닥 상승을 견인했다.',
                article_body_excerpt='반도체 강세',
                content_json={},
                created_at='2026-03-18T06:12:10+00:00',
                updated_at='2026-03-18T06:12:10+00:00',
            ),
            projections_module.NewsArticleProcessedRecord(
                processed_article_id=4002,
                business_date=BUSINESS_DATE,
                market_type='US',
                dedupe_hash='b' * 64,
                canonical_title='대형 기술주 재평가로 나스닥 반등',
                publisher_name='한국경제',
                published_at=None,
                origin_link='https://example.com/article2',
                naver_link='https://search.naver.com/article2',
                source_summary='대형 기술주 매수세가 확대됐다.',
                article_body_excerpt='기술주 강세',
                content_json={},
                created_at='2026-03-18T06:12:10+00:00',
                updated_at='2026-03-18T06:12:10+00:00',
            ),
        ]


class FakeClusterRepo:
    def __init__(self, session):
        _ = session
        self.calls = []

    async def create_cluster_bundle(self, params, article_ids):
        self.calls.append((params, list(article_ids)))
        return projections_module.ClusterRecord(
            cluster_id=7001,
            cluster_uid='51f0d9a0-9fc5-4f15-a4f9-62856f128683',
            business_date=params.business_date,
            market_type=params.market_type,
            cluster_rank=params.cluster_rank,
            title=params.title,
            summary_short=params.summary_short,
            summary_long=params.summary_long,
            analysis_paragraphs_json=params.analysis_paragraphs_json,
            tags_json=params.tags_json,
            representative_article_id=params.representative_article_id,
            article_count=params.article_count,
            created_at='2026-03-18T06:12:10+00:00',
            updated_at='2026-03-18T06:12:10+00:00',
        )


@pytest.mark.anyio
async def test_build_clusters_creates_scaffold_bundle(monkeypatch):
    session = RecordingAsyncSession()
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    monkeypatch.setattr(
        build_clusters_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )
    monkeypatch.setattr(
        build_clusters_module, 'NewsClusterWriteRepository', FakeClusterRepo
    )

    step = BuildClustersStep()
    updated_context = await step.run(fake_repository, context)

    assert updated_context.cluster_count == 1
    assert updated_context.log_messages


@pytest.mark.anyio
async def test_build_clusters_records_llm_fallback_error_context(monkeypatch):
    session = RecordingAsyncSession()
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    class FakeLlmProvider:
        def is_configured(self):
            return True

        async def enrich_cluster(self, **kwargs):
            _ = kwargs
            raise TimeoutError('provider timeout')

    monkeypatch.setattr(
        build_clusters_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )
    monkeypatch.setattr(
        build_clusters_module, 'NewsClusterWriteRepository', FakeClusterRepo
    )
    monkeypatch.setattr(build_clusters_module, 'BatchLlmProvider', FakeLlmProvider)

    updated_context = await BuildClustersStep().run(fake_repository, context)

    assert updated_context.cluster_count == 2
    warning_events = [
        event
        for event in fake_repository.events
        if event['message'] == 'Cluster enrichment used fallback response.'
    ]
    assert len(warning_events) == 2
    for event in warning_events:
        assert event['context_json']['error'] == {
            'provider': 'BatchLlmProvider',
            'errorClass': 'TimeoutError',
            'errorMessage': 'provider timeout',
        }
