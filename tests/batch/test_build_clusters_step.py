from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]

from app.core.llm import LlmRetryableError
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


class ListProcessedRepo:
    def __init__(self, articles):
        self.articles = articles

    async def list_by_business_date(self, business_date, *, market_type=None):
        _ = business_date
        return [
            article
            for article in self.articles
            if market_type is None or article.market_type == market_type
        ]


class RecordingLlmProvider:
    concurrency_limit = 4

    def __init__(self, *, configured: bool):
        self.configured = configured
        self.calls: list[dict] = []

    def is_configured(self):
        return self.configured

    async def enrich_cluster(self, **kwargs):
        if not self.configured:
            raise AssertionError('Unconfigured provider must not be called.')
        self.calls.append(kwargs)
        representative = kwargs['articles'][0]
        return {
            'title': representative['title'],
            'summary_short': representative['summary'],
            'summary_long': representative['summary'],
            'tags': ['tag'],
            'analysis_paragraphs': ['analysis'],
            'representative_article_index': 0,
        }


def _processed_article(
    article_id: int,
    *,
    market_type: str,
    title: str,
    published_at: datetime,
):
    return projections_module.NewsArticleProcessedRecord(
        processed_article_id=article_id,
        business_date=BUSINESS_DATE,
        market_type=market_type,
        dedupe_hash=f'{article_id:064x}',
        canonical_title=title,
        publisher_name='publisher',
        published_at=published_at,
        origin_link=f'https://example.com/{article_id}',
        naver_link=None,
        source_summary=f'summary {article_id}',
        article_body_excerpt=f'excerpt {article_id}',
        content_json={},
        created_at=published_at,
        updated_at=published_at,
    )


async def _run_step_with_articles(articles, *, provider, max_per_market=12):
    session = RecordingAsyncSession()
    batch_repository = FakeBatchRepository(session=session, events=[])
    processed_repository = ListProcessedRepo(articles)
    cluster_repository = FakeClusterRepo(session)
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )
    step = BuildClustersStep(
        processed_repo_factory=lambda _session: processed_repository,
        cluster_repo_factory=lambda _session: cluster_repository,
        llm_provider_factory=lambda: provider,
        settings=SimpleNamespace(
            batch_max_clusters_per_market=max_per_market,
        ),
    )

    updated_context = await step.run(batch_repository, context)
    return updated_context, batch_repository, cluster_repository


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

    class FakeLlmProvider:
        def is_configured(self):
            return False

    monkeypatch.setattr(
        build_clusters_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )
    monkeypatch.setattr(
        build_clusters_module, 'NewsClusterWriteRepository', FakeClusterRepo
    )
    monkeypatch.setattr(build_clusters_module, 'BatchLlmProvider', FakeLlmProvider)

    step = BuildClustersStep()
    updated_context = await step.run(fake_repository, context)

    assert updated_context.cluster_count == 2
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
            raise TimeoutError(
                '429 RESOURCE_EXHAUSTED secret-token RetryInfo '
                'https://generativelanguage.googleapis.com'
            )

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
            'code': 'AI_PROVIDER_REQUEST_FAILED',
            'errorClass': 'TimeoutError',
            'message': 'AI provider request failed; fallback content was used.',
        }
    serialized = repr(fake_repository.events)
    assert 'secret-token' not in serialized
    assert 'RetryInfo' not in serialized
    assert 'googleapis.com' not in serialized


@pytest.mark.anyio
async def test_build_clusters_propagates_retryable_llm_error(monkeypatch):
    session = RecordingAsyncSession()
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    class RetryableLlmProvider:
        concurrency_limit = 1

        def is_configured(self):
            return True

        async def enrich_cluster(self, **_kwargs):
            raise LlmRetryableError(retry_after_seconds=30)

    monkeypatch.setattr(
        build_clusters_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )
    monkeypatch.setattr(
        build_clusters_module, 'NewsClusterWriteRepository', FakeClusterRepo
    )
    monkeypatch.setattr(
        build_clusters_module,
        'BatchLlmProvider',
        RetryableLlmProvider,
    )

    with pytest.raises(LlmRetryableError):
        await BuildClustersStep().run(fake_repository, context)
    assert all('secret-token' not in reason for reason in context.partial_reasons)


@pytest.mark.anyio
async def test_build_clusters_falls_back_when_llm_enrichment_is_malformed(
    monkeypatch,
):
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
            return {'title': 'LLM title', 'tags': 'not-a-list'}

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
        assert event['context_json']['fallbackReason'] == 'llm_malformed_response'
        assert event['context_json']['error'] == {
            'code': 'AI_PROVIDER_RESPONSE_INVALID',
            'errorClass': 'ValueError',
            'message': (
                'AI provider returned an invalid response; fallback content was used.'
            ),
        }


@pytest.mark.anyio
async def test_build_clusters_bounds_llm_enrichment_concurrency(monkeypatch):
    session = RecordingAsyncSession()
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    class TrackingLlmProvider:
        concurrency_limit = 2

        def __init__(self):
            self.active = 0
            self.max_active = 0

        def is_configured(self):
            return True

        async def enrich_cluster(self, **kwargs):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {
                'title': kwargs['articles'][0]['title'],
                'summary_short': kwargs['articles'][0]['summary'],
                'summary_long': kwargs['articles'][0]['summary'],
                'tags': ['tag'],
                'analysis_paragraphs': ['analysis'],
                'representative_article_index': 0,
            }

    llm_provider = TrackingLlmProvider()
    monkeypatch.setattr(
        build_clusters_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )
    monkeypatch.setattr(
        build_clusters_module, 'NewsClusterWriteRepository', FakeClusterRepo
    )
    monkeypatch.setattr(build_clusters_module, 'BatchLlmProvider', lambda: llm_provider)

    updated_context = await BuildClustersStep().run(fake_repository, context)

    assert llm_provider.max_active == 2
    assert updated_context.cluster_count == 2


def test_cluster_ranking_is_stable_by_count_recency_and_article_id():
    base_time = datetime(2026, 3, 17, tzinfo=UTC)
    articles = [
        _processed_article(
            21,
            market_type='US',
            title='gamma market',
            published_at=base_time + timedelta(days=2),
        ),
        _processed_article(
            2,
            market_type='US',
            title='alpha market',
            published_at=base_time,
        ),
        _processed_article(
            11,
            market_type='US',
            title='beta market',
            published_at=base_time + timedelta(days=2),
        ),
        _processed_article(
            1,
            market_type='US',
            title='alpha market',
            published_at=base_time,
        ),
        _processed_article(
            20,
            market_type='US',
            title='gamma market',
            published_at=base_time + timedelta(days=1),
        ),
        _processed_article(
            10,
            market_type='US',
            title='beta market',
            published_at=base_time + timedelta(days=1),
        ),
        _processed_article(
            3,
            market_type='US',
            title='alpha market',
            published_at=base_time,
        ),
class UpsertingClusterRepo:
    """Mimic NewsClusterWriteRepository.create_cluster_bundle's ON CONFLICT
    DO UPDATE semantics: writing to an existing (market_type, cluster_rank)
    key preserves the row's id instead of replacing it, and rows nobody
    writes to are left untouched."""

    def __init__(self, existing: dict[tuple[str, int], dict] | None = None):
        self.store: dict[tuple[str, int], dict] = dict(existing or {})
        self._next_id = (
            max((row['cluster_id'] for row in self.store.values()), default=100) + 1
        )

    async def create_cluster_bundle(self, params, article_ids):
        _ = article_ids
        key = (params.market_type, params.cluster_rank)
        existing_row = self.store.get(key)
        cluster_id = existing_row['cluster_id'] if existing_row else self._next_id
        if existing_row is None:
            self._next_id += 1
        self.store[key] = {'cluster_id': cluster_id, 'title': params.title}
        return SimpleNamespace(cluster_id=cluster_id, cluster_rank=params.cluster_rank)

    async def list_cluster_ids_for_business_date(
        self, business_date, market_type, *, min_rank=None
    ):
        _ = business_date
        return [
            row['cluster_id']
            for (mt, rank), row in self.store.items()
            if mt == market_type and (min_rank is None or rank > min_rank)
        ]

    async def delete_clusters_by_ids(self, cluster_ids):
        ids = set(cluster_ids)
        self.store = {
            key: row for key, row in self.store.items() if row['cluster_id'] not in ids
        }


@pytest.mark.anyio
async def test_force_rerun_failure_preserves_untouched_market_clusters():
    """A force rerun that fails partway through a later market must not
    destroy clusters (and therefore the ai_summary/page rows that reference
    them via cluster_id) belonging to a market this run never reached.
    """
    session = RecordingAsyncSession()
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=True,
        rebuild_page_only=False,
    )

    base_time = datetime(2026, 3, 17, tzinfo=UTC)
    kr_article = _processed_article(
        1, market_type='KR', title='코스피 상승', published_at=base_time
    )
    us_article = _processed_article(
        2, market_type='US', title='nasdaq rally', published_at=base_time
    )
    processed_repository = ListProcessedRepo([kr_article, us_article])
    cluster_repository = UpsertingClusterRepo(
        existing={
            ('KR', 1): {'cluster_id': 201, 'title': 'old KR cluster'},
            ('US', 1): {'cluster_id': 301, 'title': 'old US cluster'},
        }
    )

    class FailOnUsProvider:
        concurrency_limit = 1

        def is_configured(self):
            return True

        async def enrich_cluster(self, *, market_type, **kwargs):
            _ = kwargs
            if market_type == 'US':
                raise LlmRetryableError()
            return {
                'title': 'new KR cluster',
                'summary_short': 'short',
                'summary_long': 'long',
                'tags': [],
                'analysis_paragraphs': [],
                'representative_article_index': 0,
            }

    step = BuildClustersStep(
        processed_repo_factory=lambda _session: processed_repository,
        cluster_repo_factory=lambda _session: cluster_repository,
        llm_provider_factory=FailOnUsProvider,
        settings=SimpleNamespace(
            batch_max_clusters_per_market=12,
            batch_clustering_processed_article_limit=5000,
        ),
    )

    with pytest.raises(LlmRetryableError):
        await step.run(fake_repository, context)

    # KR is processed first (alphabetically before US) and completes, so its
    # existing cluster id must be preserved by the upsert rather than
    # replaced with a new row.
    assert cluster_repository.store[('KR', 1)] == {
        'cluster_id': 201,
        'title': 'new KR cluster',
    }
    # US never had its enrichment persisted -- its previously published
    # cluster must remain completely untouched by the failed run.
    assert cluster_repository.store[('US', 1)] == {
        'cluster_id': 301,
        'title': 'old US cluster',
    }


    ]

    ranked = build_clusters_module._rank_market_clusters(
        build_clusters_module._group_articles(articles)
    )

    assert [
        [article.processed_article_id for article in cluster] for cluster in ranked
    ] == [
        [3, 2, 1],
        [11, 10],
        [21, 20],
    ]


@pytest.mark.anyio
async def test_cluster_topology_is_independent_of_provider_configuration():
    base_time = datetime(2026, 3, 17, tzinfo=UTC)
    articles = [
        _processed_article(
            article_id,
            market_type='US',
            title=f'topic{article_id // 2} signal{article_id // 2}',
            published_at=base_time + timedelta(minutes=article_id),
        )
        for article_id in range(1, 7)
    ]
    configured_provider = RecordingLlmProvider(configured=True)
    unconfigured_provider = RecordingLlmProvider(configured=False)

    _, _, configured_clusters = await _run_step_with_articles(
        articles,
        provider=configured_provider,
    )
    _, _, unconfigured_clusters = await _run_step_with_articles(
        list(reversed(articles)),
        provider=unconfigured_provider,
    )

    assert [article_ids for _, article_ids in configured_clusters.calls] == [
        article_ids for _, article_ids in unconfigured_clusters.calls
    ]
    assert len(configured_provider.calls) == len(configured_clusters.calls)
    assert unconfigured_provider.calls == []


@pytest.mark.anyio
async def test_large_market_inputs_cap_persisted_clusters_and_llm_calls(caplog):
    base_time = datetime(2026, 3, 17, tzinfo=UTC)
    articles = [
        *[
            _processed_article(
                article_id,
                market_type='US',
                title=f'ustopic{article_id} ussignal{article_id}',
                published_at=base_time + timedelta(minutes=article_id),
            )
            for article_id in range(1, 81)
        ],
        *[
            _processed_article(
                article_id,
                market_type='KR',
                title=f'krtopic{article_id} krsignal{article_id}',
                published_at=base_time + timedelta(minutes=article_id),
            )
            for article_id in range(1001, 1161)
        ],
    ]
    provider = RecordingLlmProvider(configured=True)
    caplog.set_level(
        'INFO',
        logger='app.batch.steps.build_clusters',
    )

    context, batch_repository, cluster_repository = await _run_step_with_articles(
        articles,
        provider=provider,
    )

    assert context.cluster_count == 24
    assert len(cluster_repository.calls) == 24
    assert len(provider.calls) == 24
    selection_events = {
        event['context_json']['marketType']: event['context_json']
        for event in batch_repository.events
        if event['message'] == 'Selected cluster candidates for persistence.'
    }
    assert selection_events == {
        'KR': {
            'marketType': 'KR',
            'candidateCount': 160,
            'selectedCount': 12,
            'omittedCount': 148,
            'maxClustersPerMarket': 12,
        },
        'US': {
            'marketType': 'US',
            'candidateCount': 80,
            'selectedCount': 12,
            'omittedCount': 68,
            'maxClustersPerMarket': 12,
        },
    }
    assert 'candidate=160, selected=12, omitted=148' in ' '.join(context.log_messages)
    assert 'candidate_count=160 selected_count=12 omitted_count=148' in caplog.text
