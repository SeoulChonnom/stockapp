from __future__ import annotations

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    jsonable,
    load_module,
    normalize_sql,
)

processed_repo_module = load_module('app.db.repositories.news_article_processed_repo')
projections_module = load_module('app.db.repositories.projections')

NewsArticleProcessedRepository = processed_repo_module.NewsArticleProcessedRepository
NewsArticleProcessedCreateParams = projections_module.NewsArticleProcessedCreateParams
NewsArticleRawProcessedMapCreateParams = (
    projections_module.NewsArticleRawProcessedMapCreateParams
)


@pytest.mark.anyio
async def test_get_or_create_processed_article_inserts_when_missing():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'processed_article_id': 4001,
                        'business_date': '2026-03-17',
                        'market_type': 'US',
                        'dedupe_hash': 'a' * 64,
                        'canonical_title': '엔비디아 급등에 반도체 강세',
                        'publisher_name': '매일경제',
                        'published_at': '2026-03-17T23:15:00+00:00',
                        'origin_link': 'https://example.com/article1',
                        'naver_link': 'https://search.naver.com/article1',
                        'source_summary': '반도체 업종 강세가 나스닥 상승을 견인했다.',
                        'article_body_excerpt': '반도체 강세',
                        'content_json': {},
                        'created_at': '2026-03-18T06:12:10+00:00',
                        'updated_at': '2026-03-18T06:12:10+00:00',
                    }
                ]
            )
        ]
    )
    repo = NewsArticleProcessedRepository(session)

    result = await repo.get_or_create_processed_article(
        NewsArticleProcessedCreateParams(
            business_date='2026-03-17',
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
        )
    )

    assert jsonable(result)['processed_article_id'] == 4001
    sql = normalize_sql(session.statements[0])
    assert 'news_article_processed' in sql
    assert 'dedupe_hash' in sql


@pytest.mark.anyio
async def test_link_raw_to_processed_uses_on_conflict_do_nothing():
    session = RecordingAsyncSession()
    repo = NewsArticleProcessedRepository(session)

    await repo.link_raw_to_processed(
        NewsArticleRawProcessedMapCreateParams(
            raw_article_id=1, processed_article_id=4001
        )
    )

    sql = normalize_sql(session.statements[0])
    assert 'news_article_raw_processed_map' in sql
    assert 'do nothing' in sql.lower()
