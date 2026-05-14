from __future__ import annotations

from dataclasses import dataclass

import pytest

from tests.support import BUSINESS_DATE, DummyResult, RecordingAsyncSession, load_module

dedupe_module = load_module('app.batch.steps.dedupe_articles')
projections_module = load_module('app.db.repositories.projections')

DedupeArticlesStep = dedupe_module.DedupeArticlesStep
BatchExecutionContext = load_module('app.batch.models').BatchExecutionContext


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

    step = DedupeArticlesStep()
    updated_context = await step.run(fake_repository, context)

    assert updated_context.processed_news_count == 1
    assert updated_context.log_messages
