from __future__ import annotations

import pytest

pytest.importorskip("sqlalchemy")

from tests.support import DummyResult, RecordingAsyncSession, jsonable, load_module, normalize_sql

raw_repo_module = load_module("app.db.repositories.news_article_raw_repo")

NewsArticleRawRepository = raw_repo_module.NewsArticleRawRepository


@pytest.mark.anyio
async def test_list_articles_by_business_date_filters_business_date(sample_raw_article_rows):
    session = RecordingAsyncSession(results=[DummyResult(sample_raw_article_rows)])
    repo = NewsArticleRawRepository(session)

    result = await repo.list_articles_by_business_date(sample_raw_article_rows[0]["business_date"])

    assert [row.raw_article_id for row in result] == [1, 2]
    sql = normalize_sql(session.statements[0])
    assert "news_article_raw" in sql
    assert "business_date" in sql
