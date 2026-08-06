from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest  # pyright: ignore[reportMissingImports]

from tests.market_context_fakes import CompleteMarketContextRepository
from tests.support import RecordingAsyncSession, load_module

batch_models_module = load_module('app.batch.models')
steps_module = load_module('app.batch.steps')
projections_module = load_module('app.db.repositories.projections')

BatchExecutionContext = batch_models_module.BatchExecutionContext
BuildPageSnapshotStep = steps_module.BuildPageSnapshotStep
CollectMarketIndicesStep = steps_module.CollectMarketIndicesStep
GenerateAiSummariesStep = steps_module.GenerateAiSummariesStep
AiSummaryRecord = projections_module.AiSummaryRecord
MarketIndexDailyRecord = projections_module.MarketIndexDailyRecord


@dataclass
class EventRepository:
    session: RecordingAsyncSession
    events: list[tuple[str, str]]

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        _ = kwargs
        self.events.append((step_code, message))


@dataclass
class RichEventRepository:
    session: RecordingAsyncSession
    events: list[dict]

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        self.events.append({'step_code': step_code, 'message': message, **kwargs})


def build_context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )


@pytest.mark.anyio
async def test_collect_news_step_skips_provider_when_rebuild_page_only(monkeypatch):
    collect_module = load_module('app.batch.steps.collect_news')

    class FailingProvider:
        def __init__(self):
            raise AssertionError('provider must not be created')

    monkeypatch.setattr(collect_module, 'NaverNewsProvider', FailingProvider)

    repository = RichEventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.rebuild_page_only = True

    updated_context = await collect_module.CollectNewsStep().run(repository, context)

    assert updated_context.raw_news_count == 0
    assert updated_context.log_messages == [
        'Skipped news collection because rebuild_page_only=true.'
    ]


@pytest.mark.anyio
async def test_collect_news_step_preserves_successful_keyword_when_one_fails(
    monkeypatch,
):
    collect_module = load_module('app.batch.steps.collect_news')
    provider_module = load_module('app.batch.providers.naver_news')

    class FakeKeywordRepo:
        def __init__(self, session):
            _ = session

        async def list_active_keywords(self, *, provider_name):
            return [
                projections_module.NewsSearchKeywordRecord(
                    keyword_id=1,
                    provider_name=provider_name,
                    market_type='US',
                    keyword='broken',
                    is_active=True,
                    priority=1,
                    created_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                ),
                projections_module.NewsSearchKeywordRecord(
                    keyword_id=2,
                    provider_name=provider_name,
                    market_type='KR',
                    keyword='working',
                    is_active=True,
                    priority=2,
                    created_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                    updated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                ),
            ]

    class FakeRawRepo:
        def __init__(self, session):
            _ = session
            self.inserted = []

        async def insert_articles(self, articles):
            self.inserted.extend(articles)
            return len(articles)

        async def count_articles_by_business_date(self, business_date):
            _ = business_date
            return len(self.inserted)

    class FakeProvider:
        def is_configured(self):
            return True

        async def collect_for_keyword(
            self,
            *,
            keyword_record,
            business_date,
            window_start_at,
            window_end_at,
        ):
            _ = (business_date, window_start_at, window_end_at)
            if keyword_record.keyword == 'broken':
                raise TimeoutError('provider timeout')
            return provider_module.NaverCollectedKeywordResult(
                fetched_count=2,
                candidate_count=1,
                articles=[object()],
            )

    fake_raw_repo = FakeRawRepo(RecordingAsyncSession())
    monkeypatch.setattr(collect_module, 'NewsSearchKeywordRepository', FakeKeywordRepo)
    monkeypatch.setattr(
        collect_module, 'NewsArticleRawRepository', lambda session: fake_raw_repo
    )
    monkeypatch.setattr(collect_module, 'NaverNewsProvider', FakeProvider)

    repository = RichEventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()

    updated_context = await collect_module.CollectNewsStep().run(repository, context)

    assert updated_context.raw_news_count == 1
    assert len(fake_raw_repo.inserted) == 1
    assert updated_context.warning_messages == [
        'Failed to collect Naver news for keyword: broken'
    ]
    warning_events = [
        event
        for event in repository.events
        if event['message'] == 'Failed to collect Naver news for keyword.'
    ]
    assert len(warning_events) == 1
    assert warning_events[0]['context_json']['keyword'] == 'broken'
    assert warning_events[0]['context_json']['error'] == {
        'code': 'EXTERNAL_PROVIDER_REQUEST_FAILED',
        'errorClass': 'TimeoutError',
        'message': 'External provider request failed.',
    }


@pytest.mark.anyio
async def test_collect_market_indices_step_populates_market_index_counts(monkeypatch):
    collect_module = load_module('app.batch.steps.collect_market_indices')

    class FakeProvider:
        async def fetch_for_business_date(self, business_date):
            _ = business_date
            return [
                load_module(
                    'app.batch.providers.market_index_provider'
                ).MarketIndexFetchResult(
                    market_type='US',
                    index_code='^IXIC',
                    index_name='NASDAQ',
                    currency_code='USD',
                    source_date=date(2026, 3, 17),
                    close_price=Decimal('18250.1200'),
                    change_value=Decimal('120.3300'),
                    change_percent=Decimal('0.6600'),
                    high_price=Decimal('18300.1000'),
                    low_price=Decimal('18100.2000'),
                )
            ]

    class FakeRepo:
        def __init__(self, session):
            _ = session
            self.calls = []

        async def upsert_index(self, params):
            self.calls.append(params)

    fake_repo = FakeRepo(RecordingAsyncSession())
    monkeypatch.setattr(collect_module, 'MarketIndexProvider', FakeProvider)
    monkeypatch.setattr(
        collect_module, 'MarketIndexRepository', lambda session: fake_repo
    )

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()

    updated_context = await CollectMarketIndicesStep().run(repository, context)

    assert updated_context.collected_index_count == 1
    assert updated_context.log_messages[-1].startswith('Collected 1 market index')
    assert len(fake_repo.calls) == 1


@pytest.mark.anyio
async def test_generate_ai_summaries_step_records_ai_summary_outputs(monkeypatch):
    generate_module = load_module('app.batch.steps.generate_ai_summaries')

    class FakeClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'market_type': 'US',
                    'title': '엔비디아 강세',
                    'summary_short': '반도체 강세가 지수를 견인했다.',
                    'summary_long': '엔비디아와 반도체 섹터가 시장 반등을 이끌었다.',
                    'analysis_paragraphs_json': ['반도체 강세', '금리 안정'],
                    'tags_json': ['반도체', 'AI'],
                }
            ]

        async def get_cluster_articles(self, cluster_id):
            _ = cluster_id
            return [
                {'processed_article_id': 4001, 'article_rank': 1},
                {'processed_article_id': 4002, 'article_rank': 2},
            ]

        async def get_processed_articles(self, article_ids):
            _ = article_ids
            return [
                {
                    'id': 4001,
                    'canonical_title': '엔비디아 급등',
                    'publisher_name': '매일경제',
                    'published_at': '2026-03-17T23:15:00+00:00',
                    'origin_link': 'https://example.com/article1',
                    'naver_link': 'https://search.naver.com/article1',
                    'source_summary': '반도체 강세가 지수를 견인했다.',
                    'article_body_excerpt': '반도체 강세',
                },
                {
                    'id': 4002,
                    'canonical_title': '나스닥 반등',
                    'publisher_name': '한국경제',
                    'published_at': '2026-03-17T22:10:00+00:00',
                    'origin_link': 'https://example.com/article2',
                    'naver_link': 'https://search.naver.com/article2',
                    'source_summary': '기술주 매수세 확대',
                    'article_body_excerpt': '기술주 강세',
                },
            ]

    class FakeIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return [
                MarketIndexDailyRecord(
                    market_index_daily_id=3001,
                    business_date=date(2026, 3, 17),
                    market_type='US',
                    index_code='^IXIC',
                    index_name='NASDAQ',
                    close_price=Decimal('18250.1200'),
                    change_value=Decimal('120.3300'),
                    change_percent=Decimal('0.6600'),
                    high_price=Decimal('18300.1000'),
                    low_price=Decimal('18100.2000'),
                    currency_code='USD',
                    provider_name='YFINANCE',
                )
            ]

    class FakeSummaryRepo:
        def __init__(self, session):
            _ = session
            self.rows = []

        async def insert_summary(self, params):
            self.rows.append(params)

    class FakeLlmProvider:
        def is_configured(self):
            return False

    original_global_headline = generate_module._generate_global_headline

    async def success_marked_fallback(*args, **kwargs):
        result = await original_global_headline(*args, **kwargs)
        return {**result, 'status': 'SUCCESS', 'fallback_used': True}

    fake_summary_repo = FakeSummaryRepo(RecordingAsyncSession())
    monkeypatch.setattr(generate_module, 'ClusterRepository', FakeClusterRepo)
    monkeypatch.setattr(generate_module, 'MarketIndexRepository', FakeIndexRepo)
    monkeypatch.setattr(
        generate_module, 'AiSummaryWriteRepository', lambda session: fake_summary_repo
    )
    monkeypatch.setattr(generate_module, 'BatchLlmProvider', FakeLlmProvider)
    monkeypatch.setattr(
        generate_module, '_generate_global_headline', success_marked_fallback
    )

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.cluster_count = 1

    updated_context = await GenerateAiSummariesStep().run(repository, context)

    assert updated_context.generated_summary_count == 4
    assert updated_context.ai_target_count == 4
    assert updated_context.ai_attempted_count == 4
    assert updated_context.ai_success_count == 0
    assert updated_context.ai_fallback_count == 4
    assert updated_context.ai_failed_count == 0
    assert updated_context.log_messages[-1].startswith('Generated 4 AI summary')
    assert len(fake_summary_repo.rows) == 4
    global_row = next(
        row for row in fake_summary_repo.rows if row.summary_type == 'GLOBAL_HEADLINE'
    )
    assert global_row.status == 'SUCCESS'
    assert global_row.fallback_used is True


@pytest.mark.anyio
async def test_generate_ai_summaries_skips_provider_when_rebuild_page_only():
    class FailingFactory:
        def __init__(self, _session=None):
            raise AssertionError('factory must not be created')

    class FailingLlmProvider:
        def __init__(self):
            raise AssertionError('LLM provider must not be created')

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.rebuild_page_only = True

    updated_context = await GenerateAiSummariesStep(
        cluster_repo_factory=FailingFactory,
        index_repo_factory=FailingFactory,
        summary_repo_factory=FailingFactory,
        llm_provider_factory=FailingLlmProvider,
    ).run(repository, context)

    assert updated_context.generated_summary_count == 0
    assert updated_context.ai_target_count == 0
    assert updated_context.ai_attempted_count == 0
    assert updated_context.log_messages == [
        'Skipped AI summary generation because rebuild_page_only=true.'
    ]


@pytest.mark.anyio
async def test_generate_ai_summaries_bounds_llm_calls_and_persists_model_name():
    class MultiClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'market_type': 'US',
                    'title': 'First cluster',
                    'summary_short': 'First short summary',
                    'summary_long': 'First long summary',
                    'analysis_paragraphs_json': ['first paragraph'],
                    'tags_json': ['AI'],
                },
                {
                    'id': 7002,
                    'market_type': 'US',
                    'title': 'Second cluster',
                    'summary_short': 'Second short summary',
                    'summary_long': 'Second long summary',
                    'analysis_paragraphs_json': ['second paragraph'],
                    'tags_json': ['chips'],
                },
            ]

        async def get_cluster_articles(self, cluster_id):
            return [{'processed_article_id': cluster_id + 100, 'article_rank': 1}]

        async def get_processed_articles(self, article_ids):
            return [
                {
                    'id': article_ids[0],
                    'canonical_title': f'article {article_ids[0]}',
                    'publisher_name': 'publisher',
                    'published_at': '2026-03-17T23:15:00+00:00',
                    'origin_link': 'https://example.com/article',
                    'naver_link': 'https://search.naver.com/article',
                    'source_summary': 'source summary',
                    'article_body_excerpt': 'excerpt',
                }
            ]

    class EmptyIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return []

    class RecordingSummaryRepo:
        def __init__(self, session):
            _ = session
            self.rows = []

        async def insert_summary(self, params):
            self.rows.append(params)

    class TrackingLlmProvider:
        model_name = 'test-configured-model'
        concurrency_limit = 2

        def __init__(self):
            self.active = 0
            self.max_active = 0

        def is_configured(self):
            return True

        async def _record(self, title):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return {
                'title': title,
                'body': f'{title} body',
                'background': ['background'],
                'key_themes': ['theme'],
                'outlook': 'outlook',
                'paragraphs': [f'{title} paragraph'],
            }

        async def summarize_global_headline(self, **kwargs):
            _ = kwargs
            return await self._record('global')

        async def summarize_market(self, **kwargs):
            _ = kwargs
            return await self._record('market')

        async def summarize_cluster_card(self, **kwargs):
            return await self._record(f'card-{kwargs["cluster"]["title"]}')

        async def summarize_cluster_detail(self, **kwargs):
            return await self._record(f'detail-{kwargs["cluster"]["title"]}')

    summary_repo = RecordingSummaryRepo(RecordingAsyncSession())
    llm_provider = TrackingLlmProvider()
    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()

    updated_context = await GenerateAiSummariesStep(
        cluster_repo_factory=MultiClusterRepo,
        index_repo_factory=EmptyIndexRepo,
        summary_repo_factory=lambda session: summary_repo,
        llm_provider_factory=lambda: llm_provider,
    ).run(repository, context)

    assert llm_provider.max_active == 2
    assert updated_context.generated_summary_count == 6
    assert updated_context.fallback_count == 0
    assert updated_context.ai_target_count == 6
    assert updated_context.ai_attempted_count == 6
    assert updated_context.ai_success_count == 6
    assert updated_context.ai_fallback_count == 0
    assert updated_context.ai_failed_count == 0
    assert [row.summary_type for row in summary_repo.rows] == [
        'GLOBAL_HEADLINE',
        'MARKET_SUMMARY',
        'CLUSTER_CARD_SUMMARY',
        'CLUSTER_DETAIL_ANALYSIS',
        'CLUSTER_CARD_SUMMARY',
        'CLUSTER_DETAIL_ANALYSIS',
    ]
    assert [row.cluster_id for row in summary_repo.rows] == [
        None,
        None,
        7001,
        7001,
        7002,
        7002,
    ]
    assert {row.model_name for row in summary_repo.rows} == {'test-configured-model'}


@pytest.mark.anyio
async def test_generate_ai_summaries_normalizes_string_market_metadata():
    class SingleClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'market_type': 'US',
                    'title': 'Semiconductors rally',
                    'summary_short': 'Chip stocks lifted the index.',
                    'summary_long': 'Chip stocks led a broad technology rebound.',
                    'analysis_paragraphs_json': ['Chip demand improved.'],
                    'tags_json': ['chips', 'AI'],
                }
            ]

        async def get_cluster_articles(self, cluster_id):
            _ = cluster_id
            return [{'processed_article_id': 4001, 'article_rank': 1}]

        async def get_processed_articles(self, article_ids):
            _ = article_ids
            return [
                {
                    'id': 4001,
                    'canonical_title': 'Chip stocks rally',
                    'publisher_name': 'Example News',
                    'published_at': '2026-03-17T23:15:00+00:00',
                    'origin_link': 'https://example.com/article',
                    'naver_link': 'https://search.naver.com/article',
                    'source_summary': 'Chip stocks lifted the index.',
                    'article_body_excerpt': 'Chip stocks rallied.',
                }
            ]

    class EmptyIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return []

    class RecordingSummaryRepo:
        def __init__(self, session):
            _ = session
            self.rows = []

        async def insert_summary(self, params):
            self.rows.append(params)

    class MalformedMarketLlmProvider:
        model_name = 'test-configured-model'
        concurrency_limit = 1

        def is_configured(self):
            return True

        async def summarize_global_headline(self, **kwargs):
            _ = kwargs
            return {'title': 'Global headline', 'body': 'Global body'}

        async def summarize_market(self, **kwargs):
            _ = kwargs
            return {
                'title': 'Market title',
                'body': 'Market body',
                'background': 'this must not become characters',
                'key_themes': ['AI'],
                'outlook': 'Outlook text',
            }

        async def summarize_cluster_card(self, **kwargs):
            return {
                'title': kwargs['cluster']['title'],
                'body': kwargs['cluster']['summary'],
            }

        async def summarize_cluster_detail(self, **kwargs):
            return {
                'title': kwargs['cluster']['title'],
                'body': kwargs['cluster']['summary'],
                'paragraphs': ['Detailed paragraph.'],
            }

    summary_repo = RecordingSummaryRepo(RecordingAsyncSession())
    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()

    updated_context = await GenerateAiSummariesStep(
        cluster_repo_factory=SingleClusterRepo,
        index_repo_factory=EmptyIndexRepo,
        summary_repo_factory=lambda session: summary_repo,
        llm_provider_factory=MalformedMarketLlmProvider,
    ).run(repository, context)

    market_row = next(
        row for row in summary_repo.rows if row.summary_type == 'MARKET_SUMMARY'
    )
    assert updated_context.fallback_count == 0
    assert updated_context.ai_target_count == 4
    assert updated_context.ai_attempted_count == 4
    assert updated_context.ai_success_count == 4
    assert updated_context.ai_fallback_count == 0
    assert updated_context.ai_failed_count == 0
    assert repository.events == []
    assert market_row.fallback_used is False
    assert market_row.status == 'SUCCESS'
    assert market_row.metadata_json['background'] == ['this must not become characters']
    assert market_row.metadata_json['keyThemes'] == ['AI']
    assert market_row.metadata_json['outlook'] == 'Outlook text'


@pytest.mark.anyio
async def test_generate_ai_summaries_step_records_fallback_error_metadata(monkeypatch):
    generate_module = load_module('app.batch.steps.generate_ai_summaries')

    class FakeClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'market_type': 'US',
                    'title': '엔비디아 강세',
                    'summary_short': '반도체 강세가 지수를 견인했다.',
                    'summary_long': '엔비디아와 반도체 섹터가 시장 반등을 이끌었다.',
                    'analysis_paragraphs_json': ['반도체 강세'],
                    'tags_json': ['반도체', 'AI'],
                }
            ]

        async def get_cluster_articles(self, cluster_id):
            _ = cluster_id
            return [{'processed_article_id': 4001, 'article_rank': 1}]

        async def get_processed_articles(self, article_ids):
            _ = article_ids
            return [
                {
                    'id': 4001,
                    'canonical_title': '엔비디아 급등',
                    'publisher_name': '매일경제',
                    'published_at': '2026-03-17T23:15:00+00:00',
                    'origin_link': 'https://example.com/article1',
                    'naver_link': 'https://search.naver.com/article1',
                    'source_summary': '반도체 강세가 지수를 견인했다.',
                    'article_body_excerpt': '반도체 강세',
                }
            ]

    class FakeIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return []

    class FakeSummaryRepo:
        def __init__(self, session):
            _ = session
            self.rows = []

        async def insert_summary(self, params):
            self.rows.append(params)

    class FakeLlmProvider:
        def is_configured(self):
            return True

        async def summarize_global_headline(self, **kwargs):
            _ = kwargs
            raise TimeoutError(
                '429 RESOURCE_EXHAUSTED secret-token RetryInfo '
                'https://generativelanguage.googleapis.com'
            )

        async def summarize_market(self, **kwargs):
            _ = kwargs
            raise TimeoutError(
                '429 RESOURCE_EXHAUSTED secret-token RetryInfo '
                'https://generativelanguage.googleapis.com'
            )

        async def summarize_cluster_card(self, **kwargs):
            _ = kwargs
            raise TimeoutError(
                '429 RESOURCE_EXHAUSTED secret-token RetryInfo '
                'https://generativelanguage.googleapis.com'
            )

        async def summarize_cluster_detail(self, **kwargs):
            _ = kwargs
            raise TimeoutError(
                '429 RESOURCE_EXHAUSTED secret-token RetryInfo '
                'https://generativelanguage.googleapis.com'
            )

    fake_summary_repo = FakeSummaryRepo(RecordingAsyncSession())
    monkeypatch.setattr(generate_module, 'ClusterRepository', FakeClusterRepo)
    monkeypatch.setattr(generate_module, 'MarketIndexRepository', FakeIndexRepo)
    monkeypatch.setattr(
        generate_module, 'AiSummaryWriteRepository', lambda session: fake_summary_repo
    )
    monkeypatch.setattr(generate_module, 'BatchLlmProvider', FakeLlmProvider)

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.cluster_count = 1

    updated_context = await GenerateAiSummariesStep().run(repository, context)

    assert updated_context.generated_summary_count == 4
    assert updated_context.fallback_count == 4
    assert updated_context.ai_target_count == 4
    assert updated_context.ai_attempted_count == 4
    assert updated_context.ai_success_count == 0
    assert updated_context.ai_fallback_count == 4
    assert updated_context.ai_failed_count == 0
    assert len(fake_summary_repo.rows) == 4
    for row in fake_summary_repo.rows:
        assert row.fallback_used is True
        assert (
            row.error_message
            == 'AI provider request failed; fallback content was used.'
        )
        assert row.metadata_json['error'] == {
            'code': 'AI_PROVIDER_REQUEST_FAILED',
            'errorClass': 'TimeoutError',
            'message': 'AI provider request failed; fallback content was used.',
        }
        serialized = repr(row)
        assert 'secret-token' not in serialized
        assert 'RetryInfo' not in serialized
        assert 'googleapis.com' not in serialized


@pytest.mark.anyio
async def test_build_page_snapshot_step_sets_page_identity_and_writes_snapshot(
    monkeypatch,
):
    build_module = load_module('app.batch.steps.build_page_snapshot')

    class FakeClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'cluster_uid': UUID('51f0d9a0-9fc5-4f15-a4f9-62856f128683'),
                    'market_type': 'US',
                    'cluster_rank': 1,
                    'title': '엔비디아 강세',
                    'summary_short': '반도체 강세가 지수를 견인했다.',
                    'summary_long': '엔비디아와 반도체 섹터가 시장 반등을 이끌었다.',
                    'analysis_paragraphs_json': ['반도체 강세', '금리 안정'],
                    'tags_json': ['반도체', 'AI'],
                    'representative_article_id': 4001,
                    'article_count': 2,
                    'representative_title': '엔비디아 급등',
                    'representative_publisher_name': '매일경제',
                    'representative_published_at': datetime(
                        2026, 3, 17, 23, 15, tzinfo=UTC
                    ),
                    'representative_origin_link': 'https://example.com/article1',
                    'representative_naver_link': 'https://search.naver.com/article1',
                }
            ]

        async def list_cluster_article_links_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'cluster_id': 7001,
                    'cluster_uid': UUID('51f0d9a0-9fc5-4f15-a4f9-62856f128683'),
                    'market_type': 'US',
                    'cluster_rank': 1,
                    'cluster_title': '엔비디아 강세',
                    'processed_article_id': 4001,
                    'article_rank': 1,
                    'title': '엔비디아 급등',
                    'publisher_name': '매일경제',
                    'published_at': datetime(2026, 3, 17, 23, 15, tzinfo=UTC),
                    'origin_link': 'https://example.com/article1',
                    'naver_link': 'https://search.naver.com/article1',
                },
                {
                    'cluster_id': 7001,
                    'cluster_uid': UUID('51f0d9a0-9fc5-4f15-a4f9-62856f128683'),
                    'market_type': 'US',
                    'cluster_rank': 1,
                    'cluster_title': '엔비디아 강세',
                    'processed_article_id': 4002,
                    'article_rank': 2,
                    'title': '나스닥 반등',
                    'publisher_name': '한국경제',
                    'published_at': datetime(2026, 3, 17, 22, 10, tzinfo=UTC),
                    'origin_link': 'https://example.com/article2',
                    'naver_link': 'https://search.naver.com/article2',
                },
            ]

    class FakeIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return [
                MarketIndexDailyRecord(
                    market_index_daily_id=3001,
                    business_date=date(2026, 3, 17),
                    market_type='US',
                    index_code='^IXIC',
                    index_name='NASDAQ',
                    close_price=Decimal('18250.1200'),
                    change_value=Decimal('120.3300'),
                    change_percent=Decimal('0.6600'),
                    high_price=Decimal('18300.1000'),
                    low_price=Decimal('18100.2000'),
                    currency_code='USD',
                    provider_name='YFINANCE',
                )
            ]

    class FakeAiSummaryRepo:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return [
                AiSummaryRecord(
                    summary_id=1,
                    batch_job_id=1001,
                    summary_type='GLOBAL_HEADLINE',
                    business_date=date(2026, 3, 17),
                    market_type=None,
                    cluster_id=None,
                    title='글로벌 헤드라인',
                    body=None,
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version='v1',
                    status='FALLBACK',
                    fallback_used=True,
                    error_message=None,
                    metadata_json={},
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                ),
                AiSummaryRecord(
                    summary_id=2,
                    batch_job_id=1001,
                    summary_type='MARKET_SUMMARY',
                    business_date=date(2026, 3, 17),
                    market_type='US',
                    cluster_id=None,
                    title='미국 시장 요약',
                    body='기술주 중심 반등',
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version='v1',
                    status='FALLBACK',
                    fallback_used=True,
                    error_message=None,
                    metadata_json={
                        'background': ['반도체 강세'],
                        'keyThemes': ['AI'],
                        'outlook': '지표 주목',
                    },
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                ),
                AiSummaryRecord(
                    summary_id=3,
                    batch_job_id=1001,
                    summary_type='CLUSTER_CARD_SUMMARY',
                    business_date=date(2026, 3, 17),
                    market_type='US',
                    cluster_id=7001,
                    title='엔비디아 강세',
                    body='반도체 강세가 지수를 견인했다.',
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version='v1',
                    status='FALLBACK',
                    fallback_used=True,
                    error_message=None,
                    metadata_json={},
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                ),
            ]

    class FakeSnapshotRepo:
        def __init__(self, session):
            _ = session
            self.calls = []

        async def get_next_version_no(self, business_date):
            self.calls.append(('get_next_version_no', business_date))
            return 4

        async def create_page(self, **kwargs):
            self.calls.append(('create_page', kwargs))
            return 501

        async def create_page_market(self, **kwargs):
            self.calls.append(('create_page_market', kwargs))
            return 1001

        async def insert_page_market_index(self, params):
            self.calls.append(('insert_page_market_index', params))

        async def insert_page_market_cluster(self, params):
            self.calls.append(('insert_page_market_cluster', params))

        async def insert_page_article_link(self, params):
            self.calls.append(('insert_page_article_link', params))

    fake_snapshot_repo = FakeSnapshotRepo(RecordingAsyncSession())
    monkeypatch.setattr(build_module, 'ClusterRepository', FakeClusterRepo)
    monkeypatch.setattr(build_module, 'MarketIndexRepository', FakeIndexRepo)
    monkeypatch.setattr(build_module, 'AiSummaryRepository', FakeAiSummaryRepo)
    monkeypatch.setattr(
        build_module, 'PageSnapshotWriteRepository', lambda session: fake_snapshot_repo
    )

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.raw_news_count = 10
    context.processed_news_count = 6
    context.cluster_count = 1

    updated_context = await BuildPageSnapshotStep(
        context_repo_factory=CompleteMarketContextRepository
    ).run(repository, context)

    assert updated_context.page_id == 501
    assert updated_context.page_version_no == 4
    call_names = [name for name, _payload in fake_snapshot_repo.calls]
    assert 'create_page' in call_names
    assert 'create_page_market' in call_names
    assert 'insert_page_market_cluster' in call_names
    article_link_calls = [
        payload
        for name, payload in fake_snapshot_repo.calls
        if name == 'insert_page_article_link'
    ]
    assert len(article_link_calls) == 2
    assert article_link_calls[0]['display_order'] == 1
    assert article_link_calls[0]['processed_article_id'] == 4001
    assert article_link_calls[1]['display_order'] == 2
    assert article_link_calls[1]['processed_article_id'] == 4002


@pytest.mark.anyio
async def test_build_page_snapshot_step_uses_per_market_news_counts(monkeypatch):
    """create_page_market must receive each market's own raw/processed news
    counts, not the job-wide total copied into both markets."""
    build_module = load_module('app.batch.steps.build_page_snapshot')

    class EmptyClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'cluster_uid': UUID('51f0d9a0-9fc5-4f15-a4f9-62856f128683'),
                    'market_type': 'US',
                    'cluster_rank': 1,
                    'title': '엔비디아 강세',
                    'summary_short': '반도체 강세',
                    'summary_long': '반도체 강세가 시장을 견인했다.',
                    'analysis_paragraphs_json': [],
                    'tags_json': [],
                    'representative_article_id': 4001,
                    'article_count': 1,
                    'representative_title': '엔비디아 급등',
                    'representative_publisher_name': '매일경제',
                    'representative_published_at': datetime(
                        2026, 3, 17, 23, 15, tzinfo=UTC
                    ),
                    'representative_origin_link': 'https://example.com/article1',
                    'representative_naver_link': 'https://search.naver.com/article1',
                }
            ]

        async def list_cluster_article_links_by_business_date(self, business_date):
            _ = business_date
            return []

    class EmptyIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return []

    class EmptyAiSummaryRepo:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return []

    class FakeSnapshotRepo:
        def __init__(self, session):
            _ = session
            self.calls = []

        async def get_next_version_no(self, business_date):
            _ = business_date
            return 1

        async def create_page(self, **kwargs):
            self.calls.append(('create_page', kwargs))
            return 501

        async def create_page_market(self, **kwargs):
            self.calls.append(('create_page_market', kwargs))
            return 1001

        async def insert_page_market_index(self, params):
            _ = params

        async def insert_page_market_cluster(self, params):
            _ = params

        async def insert_page_article_link(self, params):
            _ = params

    fake_snapshot_repo = FakeSnapshotRepo(RecordingAsyncSession())
    monkeypatch.setattr(build_module, 'ClusterRepository', EmptyClusterRepo)
    monkeypatch.setattr(build_module, 'MarketIndexRepository', EmptyIndexRepo)
    monkeypatch.setattr(build_module, 'AiSummaryRepository', EmptyAiSummaryRepo)
    monkeypatch.setattr(
        build_module, 'PageSnapshotWriteRepository', lambda session: fake_snapshot_repo
    )

    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()
    context.raw_news_count = 30
    context.processed_news_count = 18
    context.raw_news_count_by_market = {'US': 20, 'KR': 10}
    context.processed_news_count_by_market = {'US': 12, 'KR': 6}
    context.cluster_count = 1

    await BuildPageSnapshotStep(
        context_repo_factory=CompleteMarketContextRepository
    ).run(repository, context)

    market_calls = {
        kwargs['market_type']: kwargs
        for name, kwargs in fake_snapshot_repo.calls
        if name == 'create_page_market'
    }
    assert market_calls['US']['raw_news_count'] == 20
    assert market_calls['US']['processed_news_count'] == 12
    assert market_calls['KR']['raw_news_count'] == 10
    assert market_calls['KR']['processed_news_count'] == 6


@pytest.mark.anyio
async def test_build_page_snapshot_drops_malformed_market_metadata_fields():
    class MinimalClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    'id': 7001,
                    'cluster_uid': UUID('51f0d9a0-9fc5-4f15-a4f9-62856f128683'),
                    'market_type': 'US',
                    'title': '엔비디아 강세',
                    'summary_short': '반도체 강세가 지수를 견인했다.',
                    'tags_json': [],
                    'representative_article_id': 4001,
                    'article_count': 1,
                }
            ]

        async def list_cluster_article_links_by_business_date(self, business_date):
            _ = business_date
            return []

    class EmptyIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return []

    class MalformedSummaryRepo:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return [
                AiSummaryRecord(
                    summary_id=2,
                    batch_job_id=1001,
                    summary_type='MARKET_SUMMARY',
                    business_date=date(2026, 3, 17),
                    market_type='US',
                    cluster_id=None,
                    title='미국 시장 요약',
                    body='기술주 중심 반등',
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version='v1',
                    status='FALLBACK',
                    fallback_used=True,
                    error_message=None,
                    metadata_json={
                        'background': 'bad background',
                        'keyThemes': 'bad themes',
                        'outlook': ['bad outlook'],
                    },
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                )
            ]

    class RecordingSnapshotRepo:
        def __init__(self, session):
            _ = session
            self.market_calls = []

        async def get_next_version_no(self, business_date):
            _ = business_date
            return 1

        async def create_page(self, **kwargs):
            _ = kwargs
            return 501

        async def create_page_market(self, **kwargs):
            self.market_calls.append(kwargs)
            return 1001

        async def insert_page_market_cluster(self, params):
            _ = params

    snapshot_repo = RecordingSnapshotRepo(RecordingAsyncSession())
    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = build_context()

    await BuildPageSnapshotStep(
        cluster_repo_factory=MinimalClusterRepo,
        summary_repo_factory=MalformedSummaryRepo,
        index_repo_factory=EmptyIndexRepo,
        snapshot_repo_factory=lambda session: snapshot_repo,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(repository, context)

    us_market = snapshot_repo.market_calls[0]
    assert us_market['analysis_background_json'] == []
    assert us_market['analysis_key_themes_json'] == []
    assert us_market['analysis_outlook'] is None


@pytest.mark.anyio
async def test_article_content_provider_uses_summary_fallback_when_fetch_times_out():
    provider_module = load_module('app.batch.providers.article_content')
    provider = provider_module.ArticleContentProvider()

    class TimeoutClient:
        async def __aenter__(self):
            raise TimeoutError('provider timeout')

        async def __aexit__(self, exc_type, exc, tb):
            _ = (exc_type, exc, tb)
            return False

    provider._build_client = TimeoutClient

    result = await provider.fetch_article_content(
        origin_link='https://example.com/origin',
        naver_link='https://search.naver.com/article',
        fallback_summary='Provider fallback summary survives timeout.',
    )

    assert result.fallback_used is True
    assert result.body_text == 'Provider fallback summary survives timeout.'
    assert result.body_excerpt == 'Provider fallback summary survives timeout.'
    assert result.source_domain == 'example.com'
    assert result.fetched_url == 'https://example.com/origin'
    assert result.failure_details == [
        {
            'provider': 'ArticleContentProvider',
            'url': 'https://example.com/origin',
            'error_class': 'TimeoutError',
            'error_message': 'provider timeout',
        },
        {
            'provider': 'ArticleContentProvider',
            'url': 'https://search.naver.com/article',
            'error_class': 'TimeoutError',
            'error_message': 'provider timeout',
        },
    ]


@pytest.mark.anyio
async def test_market_index_provider_ignores_failed_ticker_and_keeps_partial_results(
    monkeypatch,
):
    provider_module = load_module('app.batch.providers.market_index_provider')
    provider = provider_module.MarketIndexProvider()
    monkeypatch.setattr(
        provider_module,
        'MARKET_INDEX_TICKERS',
        {
            'US': [
                ('BROKEN', 'Broken Index', 'USD', 'BROKEN'),
                ('GOOD', 'Good Index', 'USD', 'GOOD'),
            ]
        },
    )

    async def fake_fetch_single(**kwargs):
        if kwargs['ticker'] == 'BROKEN':
            raise TimeoutError('provider timeout')
        return provider_module.MarketIndexFetchResult(
            market_type=kwargs['market_type'],
            index_code=kwargs['index_code'],
            index_name=kwargs['index_name'],
            currency_code=kwargs['currency_code'],
            source_date=date(2026, 3, 17),
            close_price=Decimal('100.0000'),
            change_value=Decimal('1.0000'),
            change_percent=Decimal('1.0000'),
            high_price=Decimal('101.0000'),
            low_price=Decimal('99.0000'),
        )

    monkeypatch.setattr(provider, '_fetch_single', fake_fetch_single)

    results = await provider.fetch_for_business_date(date(2026, 3, 17))

    assert len(results) == 1
    assert results[0].index_code == 'GOOD'
    assert results[0].index_name == 'Good Index'
    assert [asdict(failure) for failure in provider.last_failures] == [
        {
            'provider': 'YFINANCE',
            'market_type': 'US',
            'ticker': 'BROKEN',
            'index_code': 'BROKEN',
            'index_name': 'Broken Index',
            'error_class': 'TimeoutError',
            'error_message': 'provider timeout',
        }
    ]
