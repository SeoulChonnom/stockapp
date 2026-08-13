from __future__ import annotations

import pytest

pytest.importorskip('sqlalchemy')

import app.db.repositories.projections as projections_module
from app.db.repositories.projections import ThemeCatalogRecord
from app.db.repositories.theme_repo import ThemeRepository
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


def test_projections_keep_only_canonical_theme_projection_names():
    assert hasattr(projections_module, 'ThemeCatalogRecord')
    assert hasattr(projections_module, 'ClusterThemeRecord')
    assert hasattr(projections_module, 'ThemeAssignmentCreateParams')
    assert not hasattr(projections_module, 'ThemeReadRecord')
    assert not hasattr(projections_module, 'ThemeRecord')
    assert not hasattr(projections_module, 'NewsClusterThemeRecord')


@pytest.mark.anyio
async def test_list_active_tree_rows_returns_typed_catalog_rows():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'code': 'MACRO',
                        'parent_code': None,
                        'label': '거시경제',
                        'description': '경제 전반과 금융시장의 거시 흐름',
                        'sort_order': 1,
                        'is_active': True,
                    }
                ]
            )
        ]
    )
    repo = ThemeRepository(session)

    result = await repo.list_active_tree_rows()

    assert result == [
        ThemeCatalogRecord(
            code='MACRO',
            parent_code=None,
            label='거시경제',
            description='경제 전반과 금융시장의 거시 흐름',
            sort_order=1,
            is_active=True,
        )
    ]
    sql = normalize_sql(session.statements[0])
    assert 'theme_catalog' in sql
    assert 'is_active = true' in sql.lower()
    assert 'parent_code' in sql


@pytest.mark.anyio
async def test_expand_active_theme_codes_empty_input_does_not_query():
    session = RecordingAsyncSession()
    repo = ThemeRepository(session)

    assert await repo.expand_active_theme_codes([]) == []
    assert session.statements == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ('requested_code', 'expanded_codes'),
    [
        (
            'MACRO',
            [
                'MACRO',
                'MACRO_ECONOMIC_DATA',
                'MACRO_ECONOMIC_DATA_INFLATION',
            ],
        ),
        (
            'MACRO_ECONOMIC_DATA',
            ['MACRO_ECONOMIC_DATA', 'MACRO_ECONOMIC_DATA_INFLATION'],
        ),
        ('MACRO_ECONOMIC_DATA_INFLATION', ['MACRO_ECONOMIC_DATA_INFLATION']),
    ],
)
async def test_expand_active_theme_codes_includes_requested_and_active_descendants(
    requested_code, expanded_codes
):
    session = RecordingAsyncSession(
        results=[DummyResult([{'code': code} for code in expanded_codes])]
    )
    repo = ThemeRepository(session)

    assert await repo.expand_active_theme_codes([requested_code]) == expanded_codes
    sql = normalize_sql(session.statements[0])
    assert 'with recursive' in sql.lower()
    assert 'is_active' in sql.lower()
    assert 'array' in sql.lower()
    assert 'any' in sql.lower()


@pytest.mark.anyio
async def test_expand_active_theme_codes_filters_inactive_descendants_and_deduplicates_roots():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {'code': 'MACRO'},
                    {'code': 'MACRO_ECONOMIC_DATA'},
                    {'code': 'MACRO_ECONOMIC_DATA_INFLATION'},
                ]
            )
        ]
    )
    repo = ThemeRepository(session)

    result = await repo.expand_active_theme_codes(
        ['MACRO', 'MACRO', 'MACRO_ECONOMIC_DATA']
    )

    assert result == [
        'MACRO',
        'MACRO_ECONOMIC_DATA',
        'MACRO_ECONOMIC_DATA_INFLATION',
    ]


@pytest.mark.anyio
async def test_validate_active_theme_codes_reports_exact_invalid_values_in_input_order():
    session = RecordingAsyncSession(results=[DummyResult([{'code': 'MACRO'}])])
    repo = ThemeRepository(session)

    result = await repo.validate_active_theme_codes(
        ['UNKNOWN', 'MACRO', 'INACTIVE', 'UNKNOWN']
    )

    assert result == ['UNKNOWN', 'INACTIVE', 'UNKNOWN']
    assert session.parameters[0] == {
        'theme_codes': (
            'UNKNOWN',
            'MACRO',
            'INACTIVE',
            'UNKNOWN',
        )
    }


@pytest.mark.anyio
async def test_validate_active_leaf_theme_codes_reports_parent_and_inactive_codes():
    session = RecordingAsyncSession(
        results=[DummyResult([{'code': 'SECTOR_SEMICONDUCTORS_MEMORY_HBM'}])]
    )
    repo = ThemeRepository(session)

    result = await repo.validate_active_leaf_theme_codes(
        ['SECTOR', 'SECTOR_SEMICONDUCTORS_MEMORY_HBM', 'INACTIVE']
    )

    assert result == ['SECTOR', 'INACTIVE']
    sql = normalize_sql(session.statements[0])
    assert 'not exists' in sql.lower()
    assert 'is_active' in sql
