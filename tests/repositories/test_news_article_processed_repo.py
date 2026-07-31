from __future__ import annotations

from datetime import date

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
    assert (
        'on conflict (business_date, market_type, dedupe_hash) do nothing'
        in sql.lower()
    )


@pytest.mark.anyio
async def test_get_or_create_processed_article_reuses_hash_only_within_date():
    business_date = date(2026, 3, 18)
    session = RecordingAsyncSession(
        results=[
            DummyResult([]),
            DummyResult(
                [
                    {
                        'processed_article_id': 4002,
                        'business_date': business_date,
                        'market_type': 'US',
                        'dedupe_hash': 'a' * 64,
                        'canonical_title': '엔비디아 급등에 반도체 강세',
                        'publisher_name': '매일경제',
                        'published_at': None,
                        'origin_link': 'https://example.com/article1',
                        'naver_link': 'https://search.naver.com/article1',
                        'source_summary': None,
                        'article_body_excerpt': None,
                        'content_json': {},
                        'created_at': '2026-03-18T06:12:10+00:00',
                        'updated_at': '2026-03-18T06:12:10+00:00',
                    }
                ]
            ),
        ]
    )
    repo = NewsArticleProcessedRepository(session)

    result = await repo.get_or_create_processed_article(
        NewsArticleProcessedCreateParams(
            business_date=business_date,
            market_type='US',
            dedupe_hash='a' * 64,
            canonical_title='엔비디아 급등에 반도체 강세',
            publisher_name='매일경제',
            published_at=None,
            origin_link='https://example.com/article1',
            naver_link='https://search.naver.com/article1',
            source_summary=None,
            article_body_excerpt=None,
            content_json={},
        )
    )

    assert result.processed_article_id == 4002
    fallback_sql = ' '.join(str(session.statements[1]).split()).lower()
    assert 'business_date = :business_date' in fallback_sql
    assert 'market_type = cast(:market_type as stock.market_type_enum)' in fallback_sql
    assert 'dedupe_hash = :dedupe_hash' in fallback_sql
    assert session.parameters[1] == {
        'business_date': business_date,
        'market_type': 'US',
        'dedupe_hash': 'a' * 64,
    }


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
