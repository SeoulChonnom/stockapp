from __future__ import annotations

from collections.abc import Sequence
from typing import get_type_hints

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
ThemeAssignmentCreateParams = projections_module.ThemeAssignmentCreateParams


def test_replace_cluster_themes_requires_persistence_assignment_params():
    annotations = get_type_hints(NewsClusterWriteRepository.replace_cluster_themes)

    assert annotations['assignments'] == Sequence[ThemeAssignmentCreateParams]


class FakeThemeRepository:
    def __init__(self, invalid_codes=()):
        self.invalid_codes = list(invalid_codes)
        self.calls = []

    async def validate_active_leaf_theme_codes(self, theme_codes):
        self.calls.append(list(theme_codes))
        return [code for code in theme_codes if code in self.invalid_codes]


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
async def test_replace_cluster_articles_invalidates_grouping_before_membership_mutation():
    session = RecordingAsyncSession()
    repo = NewsClusterWriteRepository(session)

    await repo.replace_cluster_articles(
        7001,
        [
            projections_module.NewsClusterArticleCreateParams(
                cluster_id=7001,
                processed_article_id=4001,
                article_rank=1,
            )
        ],
    )

    statements = [normalize_sql(statement).lower() for statement in session.statements]
    assert 'select id from stock.news_cluster' in statements[0]
    assert 'for update' in statements[0]
    assert statements[1].startswith('delete from stock.news_cluster_similar_group')
    assert statements[2].startswith('update stock.news_cluster set')
    assert 'article_grouping_status' in statements[2]
    assert 'article_grouping_generated_at' in statements[2]
    assert 'article_grouping_issue_code' in statements[2]
    assert statements[3].startswith('delete from stock.news_cluster_article')
    assert statements[4].startswith('insert into stock.news_cluster_article')
    assert all('market_daily_page' not in statement for statement in statements)
    assert session.parameters[2] == {
        'cluster_id': 7001,
        'status': 'UNAVAILABLE',
        'issue_code': 'SIMILARITY_GROUPING_FAILED',
    }
    assert session.commits == 0


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


@pytest.mark.anyio
async def test_replace_cluster_themes_deletes_and_batch_inserts_without_commit():
    session = RecordingAsyncSession()
    theme_repo = FakeThemeRepository()
    repo = cluster_write_repo_module.NewsClusterWriteRepository(
        session, theme_repository=theme_repo
    )

    await repo.replace_cluster_themes(
        7001,
        [
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_SEMICONDUCTORS_MEMORY_HBM',
                rank=1,
                classification_method='LLM',
            ),
            ThemeAssignmentCreateParams(
                theme_code='MACRO_MONETARY_MARKETS_FX',
                rank=2,
                classification_method='KEYWORD_FALLBACK',
            ),
        ],
    )

    assert len(session.statements) == 2
    assert normalize_sql(session.statements[0]).startswith(
        'DELETE FROM stock.news_cluster_theme'
    )
    assert 'INSERT INTO stock.news_cluster_theme' in normalize_sql(
        session.statements[1]
    )
    assert session.parameters[1] == [
        {
            'cluster_id': 7001,
            'theme_code': 'SECTOR_SEMICONDUCTORS_MEMORY_HBM',
            'rank': 1,
            'classification_method': 'LLM',
        },
        {
            'cluster_id': 7001,
            'theme_code': 'MACRO_MONETARY_MARKETS_FX',
            'rank': 2,
            'classification_method': 'KEYWORD_FALLBACK',
        },
    ]
    assert theme_repo.calls == [
        [
            'SECTOR_SEMICONDUCTORS_MEMORY_HBM',
            'MACRO_MONETARY_MARKETS_FX',
        ]
    ]
    assert session.commits == 0


@pytest.mark.anyio
async def test_replace_cluster_themes_allows_empty_assignments_to_clear_rows():
    session = RecordingAsyncSession()
    theme_repo = FakeThemeRepository()
    repo = cluster_write_repo_module.NewsClusterWriteRepository(
        session, theme_repository=theme_repo
    )

    await repo.replace_cluster_themes(7001, [])

    assert len(session.statements) == 1
    assert 'DELETE FROM stock.news_cluster_theme' in normalize_sql(
        session.statements[0]
    )
    assert theme_repo.calls == []
    assert session.commits == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    'assignments',
    [
        [
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_SEMICONDUCTORS_MEMORY_HBM',
                rank=1,
            ),
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_SEMICONDUCTORS_MEMORY_HBM',
                rank=2,
            ),
        ],
        [
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_SEMICONDUCTORS_MEMORY_HBM',
                rank=2,
            )
        ],
        [
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_SEMICONDUCTORS_MEMORY_HBM',
                rank=1,
            ),
            ThemeAssignmentCreateParams(
                theme_code='MACRO_MONETARY_MARKETS_FX',
                rank=2,
            ),
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_AUTOS_MOBILITY_EV_BATTERY',
                rank=3,
            ),
            ThemeAssignmentCreateParams(
                theme_code='SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH',
                rank=4,
            ),
        ],
    ],
)
async def test_replace_cluster_themes_rejects_invalid_ranked_sets_before_sql(
    assignments,
):
    session = RecordingAsyncSession()
    repo = cluster_write_repo_module.NewsClusterWriteRepository(
        session, theme_repository=FakeThemeRepository()
    )

    with pytest.raises(ValueError):
        await repo.replace_cluster_themes(7001, assignments)

    assert session.statements == []


@pytest.mark.anyio
async def test_replace_cluster_themes_rejects_parent_or_inactive_codes_before_delete():
    session = RecordingAsyncSession()
    theme_repo = FakeThemeRepository(
        invalid_codes=['SECTOR', 'SECTOR_SEMICONDUCTORS_MEMORY_HBM']
    )
    repo = cluster_write_repo_module.NewsClusterWriteRepository(
        session, theme_repository=theme_repo
    )

    with pytest.raises(ValueError, match='SECTOR'):
        await repo.replace_cluster_themes(
            7001,
            [
                ThemeAssignmentCreateParams(
                    theme_code='SECTOR',
                    rank=1,
                )
            ],
        )

    assert session.statements == []


@pytest.mark.anyio
async def test_replace_cluster_themes_rejects_classifier_assignment_objects():
    class ClassifierAssignment:
        theme_code = 'SECTOR_SEMICONDUCTORS_MEMORY_HBM'
        rank = 1
        classification_method = 'KEYWORD_FALLBACK'

    session = RecordingAsyncSession()
    repo = cluster_write_repo_module.NewsClusterWriteRepository(
        session, theme_repository=FakeThemeRepository()
    )

    with pytest.raises(ValueError, match='ThemeAssignmentCreateParams'):
        await repo.replace_cluster_themes(7001, [ClassifierAssignment()])

    assert session.statements == []
