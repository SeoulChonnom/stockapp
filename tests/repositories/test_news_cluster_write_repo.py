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

cluster_write_repo_module = load_module('app.db.repositories.news_cluster_write_repo')
projections_module = load_module('app.db.repositories.projections')

NewsClusterWriteRepository = cluster_write_repo_module.NewsClusterWriteRepository
NewsClusterCreateParams = projections_module.NewsClusterCreateParams


@pytest.mark.anyio
async def test_create_cluster_bundle_inserts_cluster_and_memberships():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'cluster_id': 7001,
                        'cluster_uid': '51f0d9a0-9fc5-4f15-a4f9-62856f128683',
                        'business_date': '2026-03-17',
                        'market_type': 'US',
                        'cluster_rank': 1,
                        'title': '엔비디아 및 반도체 강세에 기술주 상승',
                        'summary_short': '반도체 업종 강세가 나스닥 상승을 견인했다.',
                        'summary_long': (
                            'PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 '
                            '자극했다.'
                        ),
                        'analysis_paragraphs_json': [],
                        'tags_json': [],
                        'representative_article_id': 4001,
                        'article_count': 2,
                        'created_at': '2026-03-18T06:12:10+00:00',
                        'updated_at': '2026-03-18T06:12:10+00:00',
                    }
                ]
            )
        ]
    )
    repo = NewsClusterWriteRepository(session)

    result = await repo.create_cluster_bundle(
        NewsClusterCreateParams(
            business_date='2026-03-17',
            market_type='US',
            cluster_rank=1,
            title='엔비디아 및 반도체 강세에 기술주 상승',
            summary_short='반도체 업종 강세가 나스닥 상승을 견인했다.',
            summary_long=(
                'PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.'
            ),
            analysis_paragraphs_json=[],
            tags_json=[],
            representative_article_id=4001,
            article_count=2,
        ),
        [4001, 4002],
    )

    assert jsonable(result)['cluster_id'] == 7001
    sql = normalize_sql(session.statements[0])
    assert 'news_cluster' in sql
    assert 'representative_article_id' in sql
    assert 'analysis_paragraphs_json' in sql


@pytest.mark.anyio
async def test_list_cluster_ids_without_min_rank_omits_rank_filter_and_bind():
    """F2: when min_rank is not given, the query must not depend on an
    untyped NULL bind comparison -- the rank filter is left out of both
    the SQL text and the bound parameters entirely."""
    session = RecordingAsyncSession(results=[DummyResult([1, 2, 3])])
    repo = NewsClusterWriteRepository(session)

    cluster_ids = await repo.list_cluster_ids_for_business_date(
        '2026-03-17',
        'US',
    )

    assert cluster_ids == [1, 2, 3]
    sql = normalize_sql(session.statements[0])
    assert 'cluster_rank >' not in sql
    assert 'min_rank' not in session.parameters[0]
    assert session.parameters[0] == {
        'business_date': '2026-03-17',
        'market_type': 'US',
    }


@pytest.mark.anyio
async def test_list_cluster_ids_with_min_rank_filters_and_binds_it():
    """F2: when min_rank is given, the SQL text includes an explicit
    cluster_rank > :min_rank filter with a real (non-NULL) bound value."""
    session = RecordingAsyncSession(results=[DummyResult([4])])
    repo = NewsClusterWriteRepository(session)

    cluster_ids = await repo.list_cluster_ids_for_business_date(
        '2026-03-17',
        'US',
        min_rank=3,
    )

    assert cluster_ids == [4]
    sql = normalize_sql(session.statements[0])
    assert 'cluster_rank >' in sql
    assert session.parameters[0] == {
        'business_date': '2026-03-17',
        'market_type': 'US',
        'min_rank': 3,
    }
