from __future__ import annotations

from datetime import UTC, datetime, timezone

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    jsonable,
    load_module,
    normalize_sql,
)

keyword_repo_module = load_module('app.db.repositories.news_search_keyword_repo')
projections_module = load_module('app.db.repositories.projections')
settings_module = load_module('app.core.settings')

NewsSearchKeywordRepository = keyword_repo_module.NewsSearchKeywordRepository
NewsSearchKeywordCreateParams = projections_module.NewsSearchKeywordCreateParams
NewsSearchKeywordUpdateParams = projections_module.NewsSearchKeywordUpdateParams


@pytest.fixture(autouse=True)
def clear_settings_cache():
    settings_module.get_settings.cache_clear()
    yield
    settings_module.get_settings.cache_clear()


@pytest.mark.anyio
async def test_list_active_keywords_filters_by_provider_market_and_active_flag():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'keyword_id': 11,
                        'provider_name': 'NAVER_NEWS',
                        'market_type': 'US',
                        'keyword': '미국 증시',
                        'is_active': True,
                        'priority': 10,
                        'created_at': datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                        'updated_at': datetime(2026, 3, 18, 6, 0, tzinfo=UTC),
                    }
                ]
            )
        ]
    )
    repo = NewsSearchKeywordRepository(session)

    result = await repo.list_active_keywords(
        provider_name='NAVER_NEWS', market_type='US'
    )

    assert len(result) == 1
    assert jsonable(result[0])['keyword'] == '미국 증시'
    sql = normalize_sql(session.statements[0])
    assert 'from stock.news_search_keyword' in sql.lower()
    assert 'stock.market_type_enum' in sql
    assert session.parameters[0]['is_active'] is True


@pytest.mark.anyio
async def test_create_keyword_inserts_keyword_catalog_row():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'keyword_id': 21,
                        'provider_name': 'NAVER_NEWS',
                        'market_type': 'KR',
                        'keyword': '코스피',
                        'is_active': True,
                        'priority': 20,
                        'created_at': datetime(2026, 3, 19, 0, 0, tzinfo=UTC),
                        'updated_at': datetime(2026, 3, 19, 0, 0, tzinfo=UTC),
                    }
                ]
            )
        ]
    )
    repo = NewsSearchKeywordRepository(session)

    result = await repo.create_keyword(
        NewsSearchKeywordCreateParams(
            provider_name='NAVER_NEWS',
            market_type='KR',
            keyword='코스피',
            priority=20,
            is_active=True,
        )
    )

    assert jsonable(result)['keyword_id'] == 21
    sql = normalize_sql(session.statements[0])
    assert 'insert into stock.news_search_keyword' in sql.lower()
    assert 'stock.market_type_enum' in sql


@pytest.mark.anyio
async def test_update_keyword_sets_updated_at_and_returns_row():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'keyword_id': 21,
                        'provider_name': 'NAVER_NEWS',
                        'market_type': 'KR',
                        'keyword': '코스닥',
                        'is_active': False,
                        'priority': 30,
                        'created_at': datetime(2026, 3, 19, 0, 0, tzinfo=UTC),
                        'updated_at': datetime(2026, 3, 19, 1, 0, tzinfo=UTC),
                    }
                ]
            )
        ]
    )
    repo = NewsSearchKeywordRepository(session)

    result = await repo.update_keyword(
        keyword_id=21,
        params=NewsSearchKeywordUpdateParams(
            keyword='코스닥',
            priority=30,
            is_active=False,
        ),
    )

    assert result is not None
    assert jsonable(result)['is_active'] is False
    sql = normalize_sql(session.statements[0])
    assert 'update stock.news_search_keyword' in sql.lower()
    assert 'updated_at = now()' in sql.lower()


@pytest.mark.anyio
async def test_list_active_keywords_rejects_invalid_database_schema_before_query(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_DATABASE_SCHEMA', 'stock; DROP TABLE stock; --')
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = NewsSearchKeywordRepository(session)

    with pytest.raises(ValueError, match='Invalid PostgreSQL schema'):
        await repo.list_active_keywords(provider_name='NAVER_NEWS', market_type='US')

    assert session.statements == []
