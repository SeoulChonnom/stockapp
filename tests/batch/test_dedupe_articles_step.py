from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tests.support import BUSINESS_DATE, DummyResult, RecordingAsyncSession, load_module

dedupe_module = load_module('app.batch.steps.dedupe_articles')
projections_module = load_module('app.db.repositories.projections')

DedupeArticlesStep = dedupe_module.DedupeArticlesStep
BatchExecutionContext = load_module('app.batch.models').BatchExecutionContext
Settings = load_module('app.core.settings').Settings
NEWS_COVERAGE_INCOMPLETE = load_module('app.batch.diagnostics').NEWS_COVERAGE_INCOMPLETE


@dataclass
class FakeBatchRepository:
    session: RecordingAsyncSession
    events: list[tuple[str, str]]

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        _ = kwargs
        self.events.append((step_code, message))


class FakeRawRepo:
    def __init__(self, session):
        _ = session

    async def list_articles_by_window(self, **_kwargs):
        return [
            projections_module.NewsArticleRawRecord(
                raw_article_id=1,
                provider_name='NAVER_NEWS',
                provider_article_key='raw-1',
                market_type='US',
                business_date=BUSINESS_DATE,
                search_keyword='엔비디아',
                title='<b>엔비디아 급등에 반도체 강세</b>',
                publisher_name='매일경제',
                published_at=None,
                origin_link='https://example.com/article1',
                naver_link='https://search.naver.com/article1',
                payload_json={
                    'description': '반도체 업종 강세가 나스닥 상승을 견인했다.'
                },
                collected_at='2026-03-18T06:12:10+00:00',
                created_at='2026-03-18T06:12:10+00:00',
            ),
            projections_module.NewsArticleRawRecord(
                raw_article_id=2,
                provider_name='NAVER_NEWS',
                provider_article_key='raw-2',
                market_type='US',
                business_date=BUSINESS_DATE,
                search_keyword='엔비디아',
                title='엔비디아 급등에 반도체 강세',
                publisher_name='매일경제',
                published_at=None,
                origin_link='https://example.com/article1/',
                naver_link='https://search.naver.com/article2',
                payload_json={
                    'description': '반도체 업종 강세가 나스닥 상승을 견인했다.'
                },
                collected_at='2026-03-18T06:12:10+00:00',
                created_at='2026-03-18T06:12:10+00:00',
            ),
        ]


class FakeMarketContextRepo:
    def __init__(self, session):
        _ = session
        self.coverage_updates = []

    async def list_for_job(self, job_id):
        _ = job_id
        return [
            SimpleNamespace(
                market_type='US',
                news_window_start_at=datetime(2026, 3, 17, tzinfo=UTC),
                news_window_end_at=datetime(2026, 3, 18, tzinfo=UTC),
            )
        ]

    async def set_news_coverage_complete(self, **kwargs):
        self.coverage_updates.append(kwargs)


class FakeCollectionRunRepo:
    def __init__(self, session):
        _ = session

    async def list_complete_intervals(self, **kwargs):
        return [
            projections_module.NewsCoverageInterval(
                window_start_at=kwargs['window_start_at'],
                window_end_at=kwargs['window_end_at'],
            )
        ]


class FakeProcessedRepo:
    def __init__(self, session):
        _ = session
        self.created = []
        self.mappings = []

    async def get_or_create_processed_article(self, params):
        self.created.append(params)
        return projections_module.NewsArticleProcessedRecord(
            processed_article_id=len(self.created),
            business_date=params.business_date,
            market_type=params.market_type,
            dedupe_hash=params.dedupe_hash,
            canonical_title=params.canonical_title,
            publisher_name=params.publisher_name,
            published_at=params.published_at,
            origin_link=params.origin_link,
            naver_link=params.naver_link,
            source_summary=params.source_summary,
            article_body_excerpt=params.article_body_excerpt,
            content_json=params.content_json,
            created_at='2026-03-18T06:12:10+00:00',
            updated_at='2026-03-18T06:12:10+00:00',
        )

    async def link_raw_to_processed(self, params):
        self.mappings.append(params)


class FakeContentProvider:
    async def fetch_article_content(self, *, origin_link, naver_link, fallback_summary):
        _ = naver_link
        return load_module('app.batch.providers.article_content').ArticleContentResult(
            body_text=f'body:{origin_link}',
            body_excerpt=f'body:{origin_link}',
            source_summary=fallback_summary,
            source_domain='example.com',
            fetched_url=origin_link,
            fallback_used=False,
            failure_details=[],
        )


@pytest.mark.anyio
async def test_dedupe_articles_updates_processed_count_and_logs(monkeypatch):
    session = RecordingAsyncSession(results=[DummyResult([])])
    fake_repository = FakeBatchRepository(session=session, events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    monkeypatch.setattr(dedupe_module, 'NewsArticleRawRepository', FakeRawRepo)
    monkeypatch.setattr(
        dedupe_module, 'NewsArticleProcessedRepository', FakeProcessedRepo
    )

    step = DedupeArticlesStep(
        content_provider_factory=FakeContentProvider,
        market_context_repo_factory=FakeMarketContextRepo,
        collection_run_repo_factory=FakeCollectionRunRepo,
    )
    updated_context = await step.run(fake_repository, context)

    assert updated_context.processed_news_count == 1
    assert updated_context.log_messages


@pytest.mark.anyio
async def test_dedupe_articles_bounds_content_fetches_and_keeps_partial_results():
    class ManyRawRepo:
        def __init__(self, session):
            _ = session

        async def list_articles_by_window(self, **_kwargs):
            return [
                projections_module.NewsArticleRawRecord(
                    raw_article_id=index,
                    provider_name='NAVER_NEWS',
                    provider_article_key=f'raw-{index}',
                    market_type='US',
                    business_date=BUSINESS_DATE,
                    search_keyword='keyword',
                    title=f'article {index}',
                    publisher_name='publisher',
                    published_at=None,
                    origin_link=f'https://example.com/article-{index}',
                    naver_link=f'https://search.naver.com/article-{index}',
                    payload_json={'description': f'fallback {index}'},
                    collected_at='2026-03-18T06:12:10+00:00',
                    created_at='2026-03-18T06:12:10+00:00',
                )
                for index in range(1, 5)
            ]

    class TrackingContentProvider:
        def __init__(self):
            self.active = 0
            self.max_active = 0

        async def fetch_article_content(
            self, *, origin_link, naver_link, fallback_summary
        ):
            _ = naver_link
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            if origin_link.endswith('article-2'):
                raise TimeoutError('provider timeout')
            return load_module(
                'app.batch.providers.article_content'
            ).ArticleContentResult(
                body_text=f'body:{origin_link}',
                body_excerpt=f'body:{origin_link}',
                source_summary=fallback_summary,
                source_domain='example.com',
                fetched_url=origin_link,
                fallback_used=False,
                failure_details=[],
            )

    provider = TrackingContentProvider()
    processed_repo = FakeProcessedRepo(RecordingAsyncSession())
    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=ManyRawRepo,
        processed_repo_factory=lambda session: processed_repo,
        content_provider_factory=lambda: provider,
        market_context_repo_factory=FakeMarketContextRepo,
        collection_run_repo_factory=FakeCollectionRunRepo,
        settings=Settings(
            app_env='development',
            article_crawl_concurrency_limit=2,
        ),
    ).run(repository, context)

    assert provider.max_active == 2
    assert context.processed_news_count == 4
    assert [mapping.raw_article_id for mapping in processed_repo.mappings] == [
        1,
        2,
        3,
        4,
    ]
    assert len(processed_repo.created) == 4
    assert processed_repo.created[1].source_summary == 'fallback 2'
    fallback_events = [
        event
        for event in repository.events
        if event[1] == 'Article content fetch recorded provider failure.'
    ]
    assert fallback_events == [
        (
            DedupeArticlesStep.step_code,
            'Article content fetch recorded provider failure.',
        )
    ]


@pytest.mark.anyio
async def test_dedupe_articles_marks_batch_partial_when_ingestion_has_gap():
    class EmptyCoverageRepo:
        def __init__(self, session):
            _ = session

        async def list_complete_intervals(self, **_kwargs):
            return []

    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=FakeRawRepo,
        processed_repo_factory=FakeProcessedRepo,
        content_provider_factory=FakeContentProvider,
        market_context_repo_factory=FakeMarketContextRepo,
        collection_run_repo_factory=EmptyCoverageRepo,
    ).run(repository, context)

    assert context.raw_news_count == 2
    assert context.partial_reasons == ['US news ingestion coverage is incomplete.']
    assert context.partial_categories == {NEWS_COVERAGE_INCOMPLETE: 1}


@pytest.mark.anyio
async def test_dedupe_articles_preserves_same_raw_article_in_both_markets():
    class SharedRawRepo:
        def __init__(self, session):
            _ = session

        async def list_articles_by_window(self, **kwargs):
            market_type = kwargs['market_type']
            return [
                projections_module.NewsArticleRawRecord(
                    raw_article_id=77,
                    provider_name='NAVER_NEWS',
                    provider_article_key='shared-provider-key',
                    market_type=market_type,
                    business_date=None,
                    search_keyword=('코스피' if market_type == 'KR' else '미국 증시'),
                    title='글로벌 증시 동반 상승',
                    publisher_name='테스트뉴스',
                    published_at=datetime(2026, 3, 17, 1, 0, tzinfo=UTC),
                    origin_link='https://example.com/shared',
                    naver_link=None,
                    payload_json={'description': '양국 시장에 관련된 기사'},
                    collected_at='2026-03-17T01:01:00+00:00',
                    created_at='2026-03-17T01:01:00+00:00',
                )
            ]

    class BothMarketContextRepo:
        def __init__(self, session):
            _ = session

        async def list_for_job(self, job_id):
            _ = job_id
            return [
                SimpleNamespace(
                    market_type=market_type,
                    news_window_start_at=datetime(2026, 3, 17, tzinfo=UTC),
                    news_window_end_at=datetime(2026, 3, 18, tzinfo=UTC),
                )
                for market_type in ('KR', 'US')
            ]

        async def set_news_coverage_complete(self, **_kwargs):
            return None

    class CountingContentProvider(FakeContentProvider):
        def __init__(self):
            self.calls = 0

        async def fetch_article_content(self, **kwargs):
            self.calls += 1
            return await super().fetch_article_content(**kwargs)

    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    processed_repo = FakeProcessedRepo(repository.session)
    content_provider = CountingContentProvider()
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=SharedRawRepo,
        processed_repo_factory=lambda session: processed_repo,
        content_provider_factory=lambda: content_provider,
        market_context_repo_factory=BothMarketContextRepo,
        collection_run_repo_factory=FakeCollectionRunRepo,
    ).run(repository, context)

    assert context.raw_news_count == 1
    assert context.processed_news_count == 2
    assert [item.market_type for item in processed_repo.created] == ['KR', 'US']
    assert len(processed_repo.mappings) == 2
    assert {item.raw_article_id for item in processed_repo.mappings} == {77}
    assert content_provider.calls == 1


@pytest.mark.anyio
async def test_dedupe_articles_does_not_require_coverage_of_an_unfinished_slot():
    """The window ends mid-slot, so requiring coverage of it always fails.

    Market contexts end the news window at the wall-clock instant the job
    started (21:10:02 for job 507), while collection runs are 30-minute
    KST-aligned slots recorded only once they end. Judging coverage against
    the raw window end reported an incomplete ingest on every single run.
    """
    window_start_at = datetime(2026, 8, 13, 21, 10, 2, 505231, tzinfo=UTC)
    window_end_at = datetime(2026, 8, 14, 21, 10, 2, 505231, tzinfo=UTC)

    class MidSlotContextRepo(FakeMarketContextRepo):
        async def list_for_job(self, job_id):
            _ = job_id
            return [
                SimpleNamespace(
                    market_type='US',
                    news_window_start_at=window_start_at,
                    news_window_end_at=window_end_at,
                )
            ]

    class SlotAlignedRunRepo:
        def __init__(self, session):
            _ = session
            self.requested_ends: list[datetime] = []

        async def list_complete_intervals(self, **kwargs):
            self.requested_ends.append(kwargs['window_end_at'])
            # Contiguous 30-minute slots up to the last one that has ended.
            slots = []
            cursor = datetime(2026, 8, 13, 21, 0, tzinfo=UTC)
            while cursor < datetime(2026, 8, 14, 21, 0, tzinfo=UTC):
                slots.append(
                    projections_module.NewsCoverageInterval(
                        window_start_at=cursor,
                        window_end_at=cursor + timedelta(minutes=30),
                    )
                )
                cursor += timedelta(minutes=30)
            return slots

    context_repo_holder = {}

    def context_repo_factory(session):
        repo = MidSlotContextRepo(session)
        context_repo_holder['repo'] = repo
        return repo

    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=FakeRawRepo,
        processed_repo_factory=FakeProcessedRepo,
        content_provider_factory=FakeContentProvider,
        market_context_repo_factory=context_repo_factory,
        collection_run_repo_factory=SlotAlignedRunRepo,
        now_factory=lambda: window_end_at + timedelta(seconds=5),
    ).run(repository, context)

    assert context.partial_reasons == []
    assert context.partial_categories == {}
    assert context_repo_holder['repo'].coverage_updates[0]['coverage_complete'] is True


@pytest.mark.anyio
async def test_dedupe_articles_still_reports_a_gap_before_the_open_slot():
    """Clamping to the last finished slot must not hide a real ingest gap."""
    window_start_at = datetime(2026, 8, 13, 21, 10, 2, 505231, tzinfo=UTC)
    window_end_at = datetime(2026, 8, 14, 21, 10, 2, 505231, tzinfo=UTC)

    class MidSlotContextRepo(FakeMarketContextRepo):
        async def list_for_job(self, job_id):
            _ = job_id
            return [
                SimpleNamespace(
                    market_type='US',
                    news_window_start_at=window_start_at,
                    news_window_end_at=window_end_at,
                )
            ]

    class GappedRunRepo:
        def __init__(self, session):
            _ = session

        async def list_complete_intervals(self, **_kwargs):
            return [
                projections_module.NewsCoverageInterval(
                    window_start_at=datetime(2026, 8, 13, 21, 0, tzinfo=UTC),
                    window_end_at=datetime(2026, 8, 14, 3, 0, tzinfo=UTC),
                )
            ]

    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=FakeRawRepo,
        processed_repo_factory=FakeProcessedRepo,
        content_provider_factory=FakeContentProvider,
        market_context_repo_factory=MidSlotContextRepo,
        collection_run_repo_factory=GappedRunRepo,
        now_factory=lambda: window_end_at + timedelta(seconds=5),
    ).run(repository, context)

    assert context.partial_categories == {NEWS_COVERAGE_INCOMPLETE: 1}


@pytest.mark.anyio
async def test_dedupe_articles_merges_one_outlet_republishing_one_story():
    """Same headline, same outlet, different URLs must become one article.

    Reproduces production shape: on 2026-09-06 `www.news1.kr` published
    "코스피 상승 마감" under five distinct URLs and yna.co.kr published
    "코스피·코스닥 상승 출발" under seven, and every one of them reached the
    reader as a separate article because the stored dedupe hash includes the
    URL. A different outlet sharing the headline is a genuinely different
    article and must survive.
    """

    class RepublishedRawRepo:
        def __init__(self, session):
            _ = session

        async def list_articles_by_window(self, **_kwargs):
            same_outlet = [
                projections_module.NewsArticleRawRecord(
                    raw_article_id=index,
                    provider_name='NAVER_NEWS',
                    provider_article_key=f'news1-{index}',
                    market_type='KR',
                    business_date=BUSINESS_DATE,
                    search_keyword='코스피',
                    title='코스피 상승 마감',
                    publisher_name=None,
                    published_at=datetime(2026, 3, 17, 1, 0, tzinfo=UTC),
                    origin_link=f'https://www.news1.kr/articles/{index}',
                    naver_link=None,
                    payload_json={'description': '설명'},
                    collected_at='2026-03-17T01:01:00+00:00',
                    created_at='2026-03-17T01:01:00+00:00',
                )
                for index in range(1, 6)
            ]
            other_outlet = [
                projections_module.NewsArticleRawRecord(
                    raw_article_id=99,
                    provider_name='NAVER_NEWS',
                    provider_article_key='yna-99',
                    market_type='KR',
                    business_date=BUSINESS_DATE,
                    search_keyword='코스피',
                    title='코스피 상승 마감',
                    publisher_name=None,
                    published_at=datetime(2026, 3, 17, 1, 0, tzinfo=UTC),
                    origin_link='https://www.yna.co.kr/view/AKR99',
                    naver_link=None,
                    payload_json={'description': '설명'},
                    collected_at='2026-03-17T01:01:00+00:00',
                    created_at='2026-03-17T01:01:00+00:00',
                )
            ]
            return same_outlet + other_outlet

    class SingleMarketContextRepo:
        def __init__(self, session):
            _ = session

        async def list_for_job(self, job_id):
            _ = job_id
            return [
                SimpleNamespace(
                    market_type='KR',
                    news_window_start_at=datetime(2026, 3, 17, tzinfo=UTC),
                    news_window_end_at=datetime(2026, 3, 18, tzinfo=UTC),
                )
            ]

        async def set_news_coverage_complete(self, **_kwargs):
            return None

    class CountingContentProvider(FakeContentProvider):
        def __init__(self):
            self.calls = 0

        async def fetch_article_content(self, **kwargs):
            self.calls += 1
            return await super().fetch_article_content(**kwargs)

    repository = FakeBatchRepository(session=RecordingAsyncSession(), events=[])
    processed_repo = FakeProcessedRepo(repository.session)
    content_provider = CountingContentProvider()
    context = BatchExecutionContext(
        job_id=1002,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )

    await DedupeArticlesStep(
        raw_repo_factory=RepublishedRawRepo,
        processed_repo_factory=lambda session: processed_repo,
        content_provider_factory=lambda: content_provider,
        market_context_repo_factory=SingleMarketContextRepo,
        collection_run_repo_factory=FakeCollectionRunRepo,
    ).run(repository, context)

    # Six raw articles, two outlets, therefore two processed articles.
    assert context.raw_news_count == 6
    assert context.processed_news_count == 2
    assert [item.publisher_name for item in processed_repo.created] == [
        'news1.kr',
        'yna.co.kr',
    ]
    # Every raw article stays linked, which is what makes
    # exact_duplicate_count non-zero downstream.
    assert len(processed_repo.mappings) == 6
    assert content_provider.calls == 2
