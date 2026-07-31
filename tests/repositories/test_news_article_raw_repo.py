from __future__ import annotations

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    load_module,
    normalize_sql,
)

raw_repo_module = load_module('app.db.repositories.news_article_raw_repo')

NewsArticleRawRepository = raw_repo_module.NewsArticleRawRepository


@pytest.mark.anyio
async def test_list_articles_by_business_date_filters_business_date(
    sample_raw_article_rows,
):
    session = RecordingAsyncSession(results=[DummyResult(sample_raw_article_rows)])
    repo = NewsArticleRawRepository(session)

    result = await repo.list_articles_by_business_date(
        sample_raw_article_rows[0]['business_date']
    )

    assert [row.raw_article_id for row in result] == [1, 2]
    sql = normalize_sql(session.statements[0]).lower()
    assert 'news_article_raw' in sql
    assert 'business_date' in sql


@pytest.mark.anyio
async def test_insert_articles_deduplicates_globally_by_provider_key(
    sample_raw_article_rows,
):
    article = sample_raw_article_rows[0]
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = NewsArticleRawRepository(session)

    await repo.insert_articles(
        [
            raw_repo_module.NewsArticleRawCreateParams(
                provider_name=article['provider_name'],
                provider_article_key=article['provider_article_key'],
                market_type=article['market_type'],
                business_date=article['business_date'],
                search_keyword=article['search_keyword'],
                title=article['title'],
                publisher_name=article['publisher_name'],
                published_at=article['published_at'],
                origin_link=article['origin_link'],
                naver_link=article['naver_link'],
                payload_json=article['payload_json'],
            )
        ]
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'on conflict (provider_name, provider_article_key)' in sql


@pytest.mark.anyio
async def test_list_articles_by_window_filters_market_and_published_time(
    sample_raw_article_rows,
):
    session = RecordingAsyncSession(results=[DummyResult(sample_raw_article_rows)])
    repo = NewsArticleRawRepository(session)

    await repo.list_articles_by_window(
        market_type='US',
        window_start_at=sample_raw_article_rows[0]['published_at'],
        window_end_at=sample_raw_article_rows[0]['published_at'],
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'published_at >= null' in sql
    assert 'published_at < null' in sql
    assert 'market_type = cast(null as stock.market_type_enum)' in sql
    assert 'cast(null as stock.market_type_enum) as market_type' in sql
