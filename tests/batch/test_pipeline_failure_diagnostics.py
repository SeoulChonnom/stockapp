from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.batch.exceptions import BatchPipelineError
from app.batch.models import BatchExecutionContext
from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator
from app.batch.providers.market_index_provider import MarketIndexFetchResult
from app.batch.providers.naver_news import NaverCollectedKeywordResult
from app.batch.steps.build_clusters import BuildClustersStep
from app.batch.steps.collect_market_indices import CollectMarketIndicesStep
from app.batch.steps.collect_news import CollectNewsStep
from app.batch.steps.finalize_job import FinalizeJobStep
from app.batch.steps.generate_ai_summaries import GenerateAiSummariesStep
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


def build_context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )


@dataclass
class EventRepository:
    session: object
    events: list[dict]

    async def add_event(self, *, step_code: str, message: str, **kwargs) -> None:
        self.events.append({'step_code': step_code, 'message': message, **kwargs})


class EmptyKeywordRepo:
    def __init__(self, session: object) -> None:
        _ = session

    async def list_active_keywords(self, *, provider_name: str) -> list:
        _ = provider_name
        return []


class ConfiguredNaverProvider:
    def is_configured(self) -> bool:
        return True


class UnusedRawRepo:
    def __init__(self, session: object) -> None:
        _ = session


@pytest.mark.anyio
async def test_collect_news_empty_keyword_catalog_fails_with_specific_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect_news_module = __import__(
        'app.batch.steps.collect_news', fromlist=['CollectNewsStep']
    )
    monkeypatch.setattr(
        collect_news_module, 'NewsSearchKeywordRepository', EmptyKeywordRepo
    )
    monkeypatch.setattr(collect_news_module, 'NewsArticleRawRepository', UnusedRawRepo)

    class MustNotConstructProvider:
        def __init__(self) -> None:
            raise AssertionError('provider must not be created for an empty catalog')

    monkeypatch.setattr(
        collect_news_module, 'NaverNewsProvider', MustNotConstructProvider
    )

    with pytest.raises(BatchPipelineError) as exc_info:
        await CollectNewsStep().run(
            EventRepository(session=object(), events=[]),
            build_context(),
        )

    assert exc_info.value.error_code == 'NEWS_KEYWORDS_NOT_CONFIGURED'
    assert 'keyword' in exc_info.value.error_message.lower()


@pytest.mark.anyio
async def test_collect_news_incomplete_credentials_fail_with_specific_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect_news_module = __import__(
        'app.batch.steps.collect_news', fromlist=['CollectNewsStep']
    )
    keyword = SimpleNamespace(
        provider_name='NAVER_NEWS',
        market_type='US',
        keyword='stocks',
    )

    class SingleKeywordRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_active_keywords(self, *, provider_name: str) -> list:
            _ = provider_name
            return [keyword]

    class UnconfiguredNaverProvider:
        def is_configured(self) -> bool:
            return False

    monkeypatch.setattr(
        collect_news_module, 'NewsSearchKeywordRepository', SingleKeywordRepo
    )
    monkeypatch.setattr(collect_news_module, 'NewsArticleRawRepository', UnusedRawRepo)
    monkeypatch.setattr(
        collect_news_module, 'NaverNewsProvider', UnconfiguredNaverProvider
    )

    with pytest.raises(BatchPipelineError) as exc_info:
        await CollectNewsStep().run(
            EventRepository(session=object(), events=[]),
            build_context(),
        )

    assert exc_info.value.error_code == 'NAVER_NOT_CONFIGURED'
    assert exc_info.value.error_message == (
        'Naver news API credentials are not configured.'
    )


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [401, 403])
async def test_collect_news_naver_auth_http_error_fails_with_specific_code(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    collect_news_module = __import__(
        'app.batch.steps.collect_news', fromlist=['CollectNewsStep']
    )
    keyword = SimpleNamespace(
        provider_name='NAVER_NEWS',
        market_type='US',
        keyword='stocks',
    )

    class SingleKeywordRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_active_keywords(self, *, provider_name: str) -> list:
            _ = provider_name
            return [keyword]

    class AuthFailureProvider(ConfiguredNaverProvider):
        async def collect_for_keyword(self, **kwargs) -> NaverCollectedKeywordResult:
            _ = kwargs
            request = httpx.Request('GET', 'https://openapi.naver.com/v1/search/news')
            response = httpx.Response(status_code, request=request)
            raise httpx.HTTPStatusError(
                'Naver authentication failed',
                request=request,
                response=response,
            )

    monkeypatch.setattr(
        collect_news_module, 'NewsSearchKeywordRepository', SingleKeywordRepo
    )
    monkeypatch.setattr(collect_news_module, 'NewsArticleRawRepository', UnusedRawRepo)
    monkeypatch.setattr(collect_news_module, 'NaverNewsProvider', AuthFailureProvider)

    with pytest.raises(BatchPipelineError) as exc_info:
        await CollectNewsStep().run(
            EventRepository(session=object(), events=[]),
            build_context(),
        )

    assert exc_info.value.error_code == 'NAVER_AUTH_FAILED'


@pytest.mark.anyio
async def test_collect_news_transient_failure_is_partial_and_recounts_available_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect_news_module = __import__(
        'app.batch.steps.collect_news', fromlist=['CollectNewsStep']
    )
    keywords = [
        SimpleNamespace(
            provider_name='NAVER_NEWS',
            market_type='US',
            keyword='broken',
        ),
        SimpleNamespace(
            provider_name='NAVER_NEWS',
            market_type='KR',
            keyword='working',
        ),
    ]

    class KeywordRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_active_keywords(self, *, provider_name: str) -> list:
            _ = provider_name
            return keywords

    class RetryAwareRawRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def insert_articles(self, articles: list) -> int:
            _ = articles
            return 0

        async def count_articles_by_business_date(self, business_date: date) -> int:
            assert business_date == date(2026, 3, 17)
            return 7

    class PartiallyFailingProvider(ConfiguredNaverProvider):
        async def collect_for_keyword(
            self,
            *,
            keyword_record: object,
            business_date: date,
            window_start_at: datetime,
            window_end_at: datetime,
        ) -> NaverCollectedKeywordResult:
            _ = (business_date, window_start_at, window_end_at)
            if keyword_record.keyword == 'broken':
                raise TimeoutError('provider timeout')
            return NaverCollectedKeywordResult(
                fetched_count=1,
                candidate_count=1,
                articles=[object()],
            )

    monkeypatch.setattr(collect_news_module, 'NewsSearchKeywordRepository', KeywordRepo)
    monkeypatch.setattr(
        collect_news_module, 'NewsArticleRawRepository', RetryAwareRawRepo
    )
    monkeypatch.setattr(
        collect_news_module, 'NaverNewsProvider', PartiallyFailingProvider
    )
    repository = EventRepository(session=object(), events=[])

    context = await CollectNewsStep().run(repository, build_context())

    assert context.raw_news_count == 7
    assert any(
        'broken' in reason and 'provider timeout' in reason
        for reason in context.partial_reasons
    )
    assert any(event.get('level') == 'WARN' for event in repository.events)


@pytest.mark.anyio
async def test_raw_news_repository_counts_all_rows_for_business_date() -> None:
    session = RecordingAsyncSession(results=[DummyResult([7])])

    count = await NewsArticleRawRepository(session).count_articles_by_business_date(
        date(2026, 3, 17)
    )

    assert count == 7
    assert 'COUNT(*)' in normalize_sql(session.statements[0])
    assert session.parameters[0] == {'business_date': date(2026, 3, 17)}


@pytest.mark.anyio
async def test_collect_market_indices_surfaces_partial_ticker_failures() -> None:
    failure = SimpleNamespace(
        provider='YFINANCE',
        market_type='US',
        ticker='BROKEN',
        index_code='BROKEN',
        index_name='Broken Index',
        error_class='TimeoutError',
        error_message='provider timeout',
    )

    class PartialProvider:
        def __init__(self) -> None:
            self.last_failures = [failure]

        async def fetch_for_business_date(
            self, business_date: date
        ) -> list[MarketIndexFetchResult]:
            return [
                MarketIndexFetchResult(
                    market_type='US',
                    index_code='GOOD',
                    index_name='Good Index',
                    currency_code='USD',
                    source_date=business_date,
                    close_price=Decimal('100'),
                    change_value=Decimal('1'),
                    change_percent=Decimal('1'),
                    high_price=Decimal('101'),
                    low_price=Decimal('99'),
                )
            ]

    class RecordingIndexRepo:
        def __init__(self, session: object) -> None:
            _ = session
            self.rows = []

        async def upsert_index(self, params: object) -> None:
            self.rows.append(params)

    repository = EventRepository(session=object(), events=[])
    context = await CollectMarketIndicesStep(
        provider_factory=PartialProvider,
        index_repo_factory=RecordingIndexRepo,
    ).run(repository, build_context())

    assert context.collected_index_count == 1
    assert any(
        'BROKEN' in reason and 'provider timeout' in reason
        for reason in context.partial_reasons
    )
    warning = next(event for event in repository.events if event.get('level') == 'WARN')
    assert warning['context_json']['ticker'] == 'BROKEN'
    assert warning['context_json']['error']['errorMessage'] == 'provider timeout'


@pytest.mark.anyio
async def test_cluster_llm_fallback_increments_count_and_adds_partial_diagnostic() -> (
    None
):
    article = SimpleNamespace(
        processed_article_id=4001,
        market_type='US',
        canonical_title='Chip stocks rally',
        publisher_name='Example News',
        published_at=None,
        source_summary='Chip stocks lifted the index.',
        article_body_excerpt='Chip stocks rallied.',
    )

    class ProcessedRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_by_business_date(self, business_date: date) -> list:
            _ = business_date
            return [article]

    class ClusterRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def create_cluster_bundle(
            self, params: object, article_ids: list[int]
        ) -> object:
            _ = (params, article_ids)
            return SimpleNamespace(cluster_id=7001)

    class FailingLlmProvider:
        concurrency_limit = 1

        def is_configured(self) -> bool:
            return True

        async def enrich_cluster(self, **kwargs) -> dict:
            _ = kwargs
            raise TimeoutError('cluster provider timeout')

    repository = EventRepository(session=object(), events=[])
    context = await BuildClustersStep(
        processed_repo_factory=ProcessedRepo,
        cluster_repo_factory=ClusterRepo,
        llm_provider_factory=FailingLlmProvider,
    ).run(repository, build_context())

    assert context.fallback_count == 1
    assert any(
        'cluster provider timeout' in reason for reason in context.partial_reasons
    )
    warning = next(event for event in repository.events if event.get('level') == 'WARN')
    assert warning['context_json']['error']['errorMessage'] == (
        'cluster provider timeout'
    )


@pytest.mark.anyio
async def test_summary_llm_errors_are_in_warning_event_and_partial_diagnostics() -> (
    None
):
    cluster = {
        'id': 7001,
        'market_type': 'US',
        'title': 'Chip stocks rally',
        'summary_short': 'Chip stocks lifted the index.',
        'summary_long': 'Chip stocks led a broad technology rebound.',
        'analysis_paragraphs_json': ['Chip demand improved.'],
        'tags_json': ['chips'],
    }

    class ClusterRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_clusters_by_business_date(self, business_date: date) -> list:
            _ = business_date
            return [cluster]

        async def get_cluster_articles(self, cluster_id: int) -> list[dict]:
            _ = cluster_id
            return [{'processed_article_id': 4001}]

        async def get_processed_articles(self, article_ids: list[int]) -> list[dict]:
            _ = article_ids
            return [
                {
                    'id': 4001,
                    'canonical_title': 'Chip stocks rally',
                    'source_summary': 'Chip stocks lifted the index.',
                }
            ]

    class IndexRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def list_indices_by_business_date(self, business_date: date) -> list:
            _ = business_date
            return []

    class SummaryRepo:
        def __init__(self, session: object) -> None:
            _ = session

        async def insert_summary(self, params: object) -> None:
            _ = params

    class FailingLlmProvider:
        concurrency_limit = 2

        def is_configured(self) -> bool:
            return True

        async def summarize_global_headline(self, **kwargs) -> dict:
            _ = kwargs
            raise TimeoutError('summary provider timeout')

        async def summarize_market(self, **kwargs) -> dict:
            _ = kwargs
            raise TimeoutError('summary provider timeout')

        async def summarize_cluster_card(self, **kwargs) -> dict:
            _ = kwargs
            raise TimeoutError('summary provider timeout')

        async def summarize_cluster_detail(self, **kwargs) -> dict:
            _ = kwargs
            raise TimeoutError('summary provider timeout')

    repository = EventRepository(session=object(), events=[])
    context = await GenerateAiSummariesStep(
        cluster_repo_factory=ClusterRepo,
        index_repo_factory=IndexRepo,
        summary_repo_factory=SummaryRepo,
        llm_provider_factory=FailingLlmProvider,
    ).run(repository, build_context())

    assert context.fallback_count == 4
    assert any(
        'summary provider timeout' in reason for reason in context.partial_reasons
    )
    warning = next(event for event in repository.events if event.get('level') == 'WARN')
    assert warning['context_json']['fallbackCount'] == 4
    assert warning['context_json']['fallbackDetails'][0]['error']['errorMessage'] == (
        'summary provider timeout'
    )


@pytest.mark.anyio
async def test_finalize_job_builds_partial_message_from_warning() -> None:
    class FinalizeRepository(EventRepository):
        def __init__(self) -> None:
            super().__init__(session=object(), events=[])
            self.completed: dict | None = None

        async def mark_job_completed(self, **kwargs) -> None:
            self.completed = kwargs

    repository = FinalizeRepository()
    context = build_context()
    context.page_id = 501
    context.warning_messages.append('NASDAQ used fallback trading date 2026-03-16.')

    await FinalizeJobStep().run(repository, context)

    assert repository.completed is not None
    assert repository.completed['status'] == 'PARTIAL'
    assert repository.completed['partial_message'] == (
        'NASDAQ used fallback trading date 2026-03-16.'
    )


class OrchestratorSession:
    async def __aenter__(self) -> OrchestratorSession:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        _ = (exc_type, exc, traceback)
        return False

    def in_transaction(self) -> bool:
        return False


class OrchestratorSessionMaker:
    def __call__(self) -> OrchestratorSession:
        return OrchestratorSession()


@pytest.mark.anyio
async def test_orchestrator_failure_persists_only_last_committed_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestrator_module = __import__(
        'app.batch.orchestrators.market_daily',
        fromlist=['MarketDailyBatchOrchestrator'],
    )

    class RecordingRepository:
        def __init__(self, session: object) -> None:
            self.session = session
            self.events = []
            self.completed = None

        async def get_job_by_id(self, job_id: int) -> object:
            return SimpleNamespace(
                job_id=job_id,
                business_date=date(2026, 3, 17),
                force_run=False,
                rebuild_page_only=False,
            )

        async def add_event(self, **kwargs) -> None:
            self.events.append(kwargs)

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

        async def mark_job_completed(self, **kwargs) -> None:
            self.completed = kwargs

        async def mark_job_failed(self, **kwargs) -> None:
            self.completed = kwargs

    repository = RecordingRepository(OrchestratorSession())
    monkeypatch.setattr(
        orchestrator_module,
        'BatchJobRepository',
        lambda session: repository,
    )
    next_step_ran = False

    class ProgressStep:
        async def execute(
            self, repository: object, context: BatchExecutionContext
        ) -> BatchExecutionContext:
            _ = repository
            context.raw_news_count = 7
            context.warning_messages.append('Committed collection warning.')
            context.log_messages.append('Committed collection progress.')
            return context

    class SpecificFailureStep:
        async def execute(
            self, repository: object, context: BatchExecutionContext
        ) -> BatchExecutionContext:
            _ = repository
            context.raw_news_count = 99
            context.processed_news_count = 88
            context.cluster_count = 77
            context.warning_messages.append('Uncommitted warning.')
            context.partial_reasons.append('Uncommitted partial reason.')
            context.log_messages.append('Uncommitted progress.')
            raise BatchPipelineError(
                error_code='NAVER_NOT_CONFIGURED',
                error_message='Naver news API credentials are not configured.',
            )

    class MustNotRunStep:
        async def execute(
            self, repository: object, context: BatchExecutionContext
        ) -> BatchExecutionContext:
            nonlocal next_step_ran
            _ = repository
            next_step_ran = True
            return context

    orchestrator = MarketDailyBatchOrchestrator(
        session_maker=OrchestratorSessionMaker()
    )
    orchestrator._steps = [ProgressStep(), SpecificFailureStep(), MustNotRunStep()]

    with pytest.raises(BatchPipelineError):
        await orchestrator.run(1001)

    assert next_step_ran is False
    assert repository.completed['status'] == 'FAILED'
    assert repository.completed['error_code'] == 'NAVER_NOT_CONFIGURED'
    assert repository.completed['error_message'] == (
        'Naver news API credentials are not configured.'
    )
    assert repository.completed['raw_news_count'] == 7
    assert repository.completed['processed_news_count'] == 0
    assert repository.completed['cluster_count'] == 0
    assert repository.completed['partial_message'] == 'Committed collection warning.'
    assert repository.completed['log_summary'] == 'Committed collection progress.'
