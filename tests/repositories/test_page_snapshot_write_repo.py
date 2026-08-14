from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip('sqlalchemy')

from app.db.repositories.page_snapshot_write_repo import PageSnapshotWriteRepository
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


@pytest.mark.anyio
async def test_create_page_persists_casefolded_nfc_search_document():
    session = RecordingAsyncSession(results=[DummyResult([901])])
    repo = PageSnapshotWriteRepository(session)

    page_id = await repo.create_page(
        business_date=date(2026, 8, 13),
        version_no=1,
        page_title='  Straße  Cafe\u0301  ',
        status='READY',
        global_headline='STRASSE\tRésumé',
        search_document='strasse café strasse résumé',
        partial_message=None,
        raw_news_count=1,
        processed_news_count=1,
        cluster_count=0,
        batch_job_id=1,
        metadata_json={},
    )

    assert page_id == 901
    assert 'search_document' in normalize_sql(session.statements[0])
    assert session.parameters[0]['search_document'] == ('strasse café strasse résumé')


@pytest.mark.anyio
async def test_create_page_market_persists_search_document():
    session = RecordingAsyncSession(results=[DummyResult([901])])
    repo = PageSnapshotWriteRepository(session)

    page_market_id = await repo.create_page_market(
        page_id=1,
        market_type='US',
        display_order=1,
        market_label='미국 증시 일간 요약',
        summary_title='시장 요약',
        summary_body='시장 본문',
        analysis_background_json=['배경'],
        analysis_key_themes_json=['테마'],
        analysis_outlook='전망',
        search_document='미국 증시 일간 요약 시장 요약 시장 본문 배경 테마 전망',
        raw_news_count=1,
        processed_news_count=1,
        cluster_count=1,
        partial_message=None,
        metadata_json={},
        expected_session_date=date(2026, 8, 13),
    )

    assert page_market_id == 901
    sql = normalize_sql(session.statements[0])
    assert 'search_document' in sql
    assert (
        session.parameters[0]['search_document']
        == '미국 증시 일간 요약 시장 요약 시장 본문 배경 테마 전망'
    )


@pytest.mark.anyio
async def test_insert_page_market_cluster_returns_snapshot_cluster_id():
    session = RecordingAsyncSession(results=[DummyResult([902])])
    repo = PageSnapshotWriteRepository(session)

    snapshot_cluster_id = await repo.insert_page_market_cluster(
        {
            'page_market_id': 901,
            'cluster_id': 701,
            'cluster_uid': 'cluster-uid',
            'display_order': 1,
            'title': '클러스터 제목',
            'summary': '클러스터 요약',
            'search_document': '클러스터 제목 클러스터 요약 대표 기사 전체 기사',
            'article_count': 1,
            'tags_json': [],
            'representative_article_id': 801,
            'representative_title': '대표 기사',
            'representative_publisher_name': '매체',
            'representative_published_at': None,
            'representative_origin_link': 'https://example.com/article',
            'representative_naver_link': None,
        }
    )

    assert snapshot_cluster_id == 902
    sql = normalize_sql(session.statements[0])
    assert 'search_document' in sql
    assert session.parameters[0]['search_document'].endswith('전체 기사')


@pytest.mark.anyio
async def test_insert_page_market_cluster_themes_preserves_rank_and_does_not_commit():
    session = RecordingAsyncSession()
    repo = PageSnapshotWriteRepository(session)

    await repo.insert_page_market_cluster_themes(
        902,
        [
            {'theme_code': 'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE', 'rank': 1},
            {'theme_code': 'MACRO_MONETARY_MARKETS_FX', 'rank': 2},
            {'theme_code': 'SECTOR_FINANCIALS', 'rank': 3},
        ],
    )

    assert len(session.statements) == 1
    assert 'market_daily_page_market_cluster_theme' in normalize_sql(
        session.statements[0]
    )
    assert session.parameters[0] == [
        {
            'page_market_cluster_id': 902,
            'theme_code': 'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE',
            'rank': 1,
        },
        {
            'page_market_cluster_id': 902,
            'theme_code': 'MACRO_MONETARY_MARKETS_FX',
            'rank': 2,
        },
        {
            'page_market_cluster_id': 902,
            'theme_code': 'SECTOR_FINANCIALS',
            'rank': 3,
        },
    ]
    assert session.commits == 0


@pytest.mark.anyio
async def test_insert_page_market_cluster_themes_rejects_invalid_rank_before_sql():
    session = RecordingAsyncSession()
    repo = PageSnapshotWriteRepository(session)

    with pytest.raises(ValueError, match='rank'):
        await repo.insert_page_market_cluster_themes(
            902,
            [{'theme_code': 'SECTOR_FINANCIALS', 'rank': 4}],
        )

    assert session.statements == []
