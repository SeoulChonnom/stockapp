from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from tests.support import BUSINESS_DATE, DummyResult, RecordingAsyncSession, load_module

dedupe_module = load_module('app.batch.steps.dedupe_articles')
projections_module = load_module('app.db.repositories.projections')

DedupeArticlesStep = dedupe_module.DedupeArticlesStep
BatchExecutionContext = load_module('app.batch.models').BatchExecutionContext
Settings = load_module('app.core.settings').Settings


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

    async def list_articles_by_business_date(self, business_date, *, market_type=None):
        _ = (business_date, market_type)
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
    async def fetch_article_content(
        self, *, origin_link, naver_link, fallback_summary
    ):
        _ = naver_link
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

    step = DedupeArticlesStep(content_provider_factory=FakeContentProvider)
    updated_context = await step.run(fake_repository, context)

    assert updated_context.processed_news_count == 1
    assert updated_context.log_messages


@pytest.mark.anyio
async def test_dedupe_articles_bounds_content_fetches_and_keeps_partial_results():
    class ManyRawRepo:
        def __init__(self, session):
            _ = session

        async def list_articles_by_business_date(
            self, business_date, *, market_type=None
        ):
            _ = (business_date, market_type)
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
