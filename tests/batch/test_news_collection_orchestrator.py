from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.batch.orchestrators import news_collection as module
from app.batch.providers.naver_news import NaverCollectedKeywordResult
from app.db.repositories.projections import NewsArticleRawCreateParams


class FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        _ = (exc_type, exc, traceback)
        return False


class FakeSessionMaker:
    def __call__(self):
        return FakeSessionContext()


def _build_successful_collection(monkeypatch):
    run = SimpleNamespace(
        run_id=41,
        window_start_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 31, 0, 20, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    )
    keywords = [
        SimpleNamespace(
            keyword_id=1,
            provider_name='NAVER_NEWS',
            market_type='KR',
            keyword='코스피',
        ),
        SimpleNamespace(
            keyword_id=2,
            provider_name='NAVER_NEWS',
            market_type='US',
            keyword='미국 증시',
        ),
    ]

    class FakeJobRepo:
        instance = None
        step_run_seq = 0
        finished_step_runs: list[tuple[int, str]] = []

        def __init__(self, session, lease_token=None):
            _ = (session, lease_token)
            self.events = []
            self.completion = None
            FakeJobRepo.instance = self

        async def begin_step(self, **_kwargs):
            FakeJobRepo.step_run_seq += 1
            return FakeJobRepo.step_run_seq

        async def finish_step_run(self, *, step_run_id, status):
            FakeJobRepo.finished_step_runs.append((step_run_id, status))
            return True

        async def add_event(self, **kwargs):
            self.events.append(kwargs)

        async def mark_job_completed(self, **kwargs):
            self.completion = kwargs

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class FakeRunRepo:
        instance = None

        def __init__(self, session):
            _ = session
            self.diagnostics = []
            self.finalized = None
            FakeRunRepo.instance = self

        async def get_by_job_id(self, job_id):
            assert job_id == 3001
            return run

        async def upsert_keyword_diagnostic(self, diagnostic):
            self.diagnostics.append(diagnostic)

        async def finalize_run(self, **kwargs):
            self.finalized = kwargs

    class FakeKeywordRepo:
        def __init__(self, session):
            _ = session

        async def list_active_keywords(self, **_kwargs):
            return keywords

    class FakeRawRepo:
        def __init__(self, session):
            _ = session

        async def insert_articles(self, articles):
            return len(articles)

        async def link_articles_to_keyword(self, **_kwargs):
            return None

    class FakeProvider:
        def is_configured(self):
            return True

        async def collect_for_keyword(self, *, keyword_record, **_kwargs):
            if keyword_record.market_type == 'US':
                raise TimeoutError('provider timeout')
            return NaverCollectedKeywordResult(
                fetched_count=3,
                candidate_count=1,
                articles=[
                    NewsArticleRawCreateParams(
                        provider_name='NAVER_NEWS',
                        provider_article_key='article-1',
                        market_type='KR',
                        search_keyword='코스피',
                        title='article',
                        publisher_name=None,
                        published_at=run.window_start_at,
                        origin_link='https://example.com/1',
                        naver_link=None,
                        payload_json={},
                    )
                ],
                coverage_complete=True,
            )

    monkeypatch.setattr(module, 'BatchJobRepository', FakeJobRepo)
    monkeypatch.setattr(module, 'NewsCollectionRunRepository', FakeRunRepo)
    monkeypatch.setattr(module, 'NewsSearchKeywordRepository', FakeKeywordRepo)
    monkeypatch.setattr(module, 'NewsArticleRawRepository', FakeRawRepo)

    orchestrator = module.NaverNewsCollectionOrchestrator(
        session_maker=FakeSessionMaker(),
        provider_factory=FakeProvider,
    )
    return FakeJobRepo, orchestrator, uuid4()


@pytest.mark.anyio
async def test_news_collection_records_partial_keyword_diagnostics(monkeypatch):
    FakeJobRepo, orchestrator, _lease_token = _build_successful_collection(monkeypatch)

    await orchestrator.run(3001)

    assert module.NewsCollectionRunRepository.instance.finalized == {
        'run_id': 41,
        'total_keyword_count': 2,
        'completed_keyword_count': 1,
        'fetched_count': 3,
        'matched_count': 1,
        'inserted_count': 1,
        'coverage_complete': False,
    }
    assert [
        item.status for item in module.NewsCollectionRunRepository.instance.diagnostics
    ] == [
        'SUCCESS',
        'FAILED',
    ]
    assert FakeJobRepo.instance.completion['status'] == 'PARTIAL'
    assert FakeJobRepo.instance.completion['raw_news_count'] == 1


@pytest.mark.anyio
async def test_collection_step_run_is_closed_as_succeeded(monkeypatch):
    job_repo, orchestrator, lease_token = _build_successful_collection(monkeypatch)

    await orchestrator.run(job_id=3001, lease_token=lease_token)

    assert job_repo.finished_step_runs == [(1, 'SUCCEEDED')]


def _build_unconfigured_collection(monkeypatch):
    run = SimpleNamespace(
        run_id=71,
        window_start_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 31, 0, 20, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    )
    keyword = SimpleNamespace(
        keyword_id=1,
        provider_name='NAVER_NEWS',
        market_type='KR',
        keyword='코스피',
    )

    class FakeJobRepo:
        instance = None
        step_run_seq = 0
        finished_step_runs: list[tuple[int, str]] = []

        def __init__(self, session, lease_token=None):
            _ = (session, lease_token)
            self.events = []
            self.failure = None
            FakeJobRepo.instance = self

        async def begin_step(self, **_kwargs):
            FakeJobRepo.step_run_seq += 1
            return FakeJobRepo.step_run_seq

        async def finish_step_run(self, *, step_run_id, status):
            FakeJobRepo.finished_step_runs.append((step_run_id, status))
            return True

        async def add_event(self, **kwargs):
            self.events.append(kwargs)

        async def mark_job_failed(self, **kwargs):
            self.failure = kwargs

        async def commit(self):
            return None

        async def rollback(self):
            return None

    class FakeRunRepo:
        instance = None

        def __init__(self, session):
            _ = session
            self.finalized = None
            FakeRunRepo.instance = self

        async def get_by_job_id(self, job_id):
            assert job_id == 3001
            return run

        async def finalize_run(self, **kwargs):
            self.finalized = kwargs

    class FakeKeywordRepo:
        def __init__(self, session):
            _ = session

        async def list_active_keywords(self, **_kwargs):
            return [keyword]

    class FakeRawRepo:
        def __init__(self, session):
            _ = session

    class UnconfiguredProvider:
        def is_configured(self):
            return False

    monkeypatch.setattr(module, 'BatchJobRepository', FakeJobRepo)
    monkeypatch.setattr(module, 'NewsCollectionRunRepository', FakeRunRepo)
    monkeypatch.setattr(module, 'NewsSearchKeywordRepository', FakeKeywordRepo)
    monkeypatch.setattr(module, 'NewsArticleRawRepository', FakeRawRepo)

    orchestrator = module.NaverNewsCollectionOrchestrator(
        session_maker=FakeSessionMaker(),
        provider_factory=UnconfiguredProvider,
    )
    return FakeJobRepo, orchestrator, uuid4()


@pytest.mark.anyio
async def test_step_run_is_closed_as_failed_when_collection_fails(monkeypatch):
    job_repo, orchestrator, lease_token = _build_unconfigured_collection(monkeypatch)

    await orchestrator.run(job_id=3001, lease_token=lease_token)

    assert job_repo.finished_step_runs == [(1, 'FAILED')]
    assert job_repo.instance.failure['error_code'] == 'NAVER_NOT_CONFIGURED'


def test_market_daily_default_pipeline_never_contains_news_provider_collection():
    from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator

    orchestrator = MarketDailyBatchOrchestrator(session_maker=FakeSessionMaker())

    assert 'COLLECT_NEWS' not in [
        getattr(step, 'step_code', None) for step in orchestrator._steps
    ]


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [429, 500, 503])
async def test_news_collection_raises_sanitized_retry_for_transient_http_status(
    monkeypatch,
    caplog,
    status_code,
):
    run = SimpleNamespace(
        run_id=51,
        window_start_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 31, 0, 20, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    )
    keyword = SimpleNamespace(
        keyword_id=1,
        provider_name='NAVER_NEWS',
        market_type='KR',
        keyword='코스피',
    )

    class FakeJobRepo:
        instance = None

        def __init__(self, session, lease_token=None):
            _ = (session, lease_token)
            self.events = []
            self.commits = 0
            FakeJobRepo.instance = self

        async def add_event(self, **kwargs):
            self.events.append(kwargs)

        async def commit(self):
            self.commits += 1

    class FakeRunRepo:
        instance = None

        def __init__(self, session):
            _ = session
            self.diagnostics = []
            FakeRunRepo.instance = self

        async def get_by_job_id(self, job_id):
            assert job_id == 3001
            return run

        async def upsert_keyword_diagnostic(self, diagnostic):
            self.diagnostics.append(diagnostic)

    class FakeKeywordRepo:
        def __init__(self, session):
            _ = session

        async def list_active_keywords(self, **_kwargs):
            return [keyword]

    class FakeRawRepo:
        def __init__(self, session):
            _ = session

    class TransientProvider:
        def is_configured(self):
            return True

        async def collect_for_keyword(self, **_kwargs):
            request = httpx.Request(
                'GET',
                'https://openapi.naver.com/news?secret=sensitive',
            )
            response = httpx.Response(
                status_code,
                request=request,
                text='sensitive provider response',
            )
            raise httpx.HTTPStatusError(
                'sensitive provider failure',
                request=request,
                response=response,
            )

    monkeypatch.setattr(module, 'BatchJobRepository', FakeJobRepo)
    monkeypatch.setattr(module, 'NewsCollectionRunRepository', FakeRunRepo)
    monkeypatch.setattr(module, 'NewsSearchKeywordRepository', FakeKeywordRepo)
    monkeypatch.setattr(module, 'NewsArticleRawRepository', FakeRawRepo)
    caplog.set_level('WARNING', logger=module.__name__)

    with pytest.raises(
        module.NaverRetryableError,
        match='Temporary Naver provider failure',
    ):
        await module.NaverNewsCollectionOrchestrator(
            session_maker=FakeSessionMaker(),
            provider_factory=TransientProvider,
        ).run(3001)

    assert FakeRunRepo.instance.diagnostics[0].error_code == (
        'NAVER_TRANSIENT_FAILURE'
    )
    assert FakeJobRepo.instance.events[-1]['context_json']['statusCode'] == (
        status_code
    )
    assert FakeJobRepo.instance.commits == 2
    assert 'exception_class=HTTPStatusError' in caplog.text
    assert 'sensitive provider' not in caplog.text
    assert 'secret=sensitive' not in caplog.text


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [401, 403])
async def test_news_collection_treats_auth_http_status_as_terminal(
    monkeypatch,
    status_code,
):
    run = SimpleNamespace(
        run_id=61,
        window_start_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 31, 0, 20, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    )
    keyword = SimpleNamespace(
        keyword_id=1,
        provider_name='NAVER_NEWS',
        market_type='KR',
        keyword='코스피',
    )

    class FakeJobRepo:
        instance = None

        def __init__(self, session, lease_token=None):
            _ = (session, lease_token)
            self.completion = None
            FakeJobRepo.instance = self

        async def add_event(self, **_kwargs):
            return None

        async def mark_job_completed(self, **kwargs):
            self.completion = kwargs

        async def commit(self):
            return None

    class FakeRunRepo:
        instance = None

        def __init__(self, session):
            _ = session
            self.diagnostics = []
            FakeRunRepo.instance = self

        async def get_by_job_id(self, job_id):
            assert job_id == 3001
            return run

        async def upsert_keyword_diagnostic(self, diagnostic):
            self.diagnostics.append(diagnostic)

        async def finalize_run(self, **_kwargs):
            return None

    class FakeKeywordRepo:
        def __init__(self, session):
            _ = session

        async def list_active_keywords(self, **_kwargs):
            return [keyword]

    class FakeRawRepo:
        def __init__(self, session):
            _ = session

    class AuthFailureProvider:
        def is_configured(self):
            return True

        async def collect_for_keyword(self, **_kwargs):
            request = httpx.Request('GET', 'https://openapi.naver.com/news')
            response = httpx.Response(status_code, request=request)
            raise httpx.HTTPStatusError(
                'auth failure',
                request=request,
                response=response,
            )

    monkeypatch.setattr(module, 'BatchJobRepository', FakeJobRepo)
    monkeypatch.setattr(module, 'NewsCollectionRunRepository', FakeRunRepo)
    monkeypatch.setattr(module, 'NewsSearchKeywordRepository', FakeKeywordRepo)
    monkeypatch.setattr(module, 'NewsArticleRawRepository', FakeRawRepo)

    await module.NaverNewsCollectionOrchestrator(
        session_maker=FakeSessionMaker(),
        provider_factory=AuthFailureProvider,
    ).run(3001)

    assert FakeRunRepo.instance.diagnostics[0].error_code == 'NAVER_AUTH_FAILED'
    assert FakeJobRepo.instance.completion['status'] == 'FAILED'
    assert FakeJobRepo.instance.completion['error_code'] == 'NAVER_AUTH_FAILED'
