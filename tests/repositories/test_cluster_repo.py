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

cluster_repo_module = load_module('app.db.repositories.cluster_repo')

ClusterRepository = cluster_repo_module.ClusterRepository


@pytest.mark.anyio
async def test_get_cluster_by_uid_looks_up_external_uuid(sample_cluster_row):
    session = RecordingAsyncSession(results=[DummyResult([sample_cluster_row])])
    repo = ClusterRepository(session)

    result = await repo.get_cluster_by_uid(sample_cluster_row['cluster_uid'])

    assert jsonable(result)['id'] == sample_cluster_row['id']
    sql = normalize_sql(session.statements[0])
    assert 'cluster_uid' in sql
    assert 'news_cluster' in sql


@pytest.mark.anyio
async def test_get_cluster_articles_orders_by_article_rank(sample_cluster_article_rows):
    session = RecordingAsyncSession(results=[DummyResult(sample_cluster_article_rows)])
    repo = ClusterRepository(session)

    result = await repo.get_cluster_articles(
        sample_cluster_article_rows[0]['cluster_id']
    )

    assert [row['article_rank'] for row in jsonable(result)] == [1, 2, 3]
    sql = normalize_sql(session.statements[0])
    assert 'news_cluster_article' in sql
    assert 'article_rank' in sql


@pytest.mark.anyio
async def test_get_processed_articles_returns_requested_rows_in_order(
    sample_processed_article_rows,
):
    session = RecordingAsyncSession(
        results=[DummyResult(sample_processed_article_rows)]
    )
    repo = ClusterRepository(session)

    result = await repo.get_processed_articles(
        [row['id'] for row in sample_processed_article_rows]
    )

    assert [row['id'] for row in jsonable(result)] == [4001, 4002, 4003]
    sql = normalize_sql(session.statements[0])
    assert 'news_article_processed' in sql
    assert 'dedupe_hash' in sql
