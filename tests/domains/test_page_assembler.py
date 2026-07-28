from __future__ import annotations

from datetime import UTC, date, datetime

from tests.support import jsonable, load_module

pages_assembler_module = load_module('app.domains.pages.assembler')

assemble_daily_page_response = pages_assembler_module.assemble_daily_page_response


def test_daily_page_assembler_keeps_market_nested_article_links(
    sample_daily_page_payload,
):
    response = jsonable(assemble_daily_page_response(sample_daily_page_payload))

    assert response['pageId'] == 501
    assert response['markets'][0]['marketType'] == 'US'
    assert response['markets'][1]['marketType'] == 'KR'
    assert (
        response['markets'][0]['topClusters'][0]['representativeArticle'][
            'publisherName'
        ]
        == '매일경제'
    )
    assert (
        response['markets'][0]['articleLinks'][0]['clusterId']
        == '51f0d9a0-9fc5-4f15-a4f9-62856f128683'
    )
    assert len(response['markets'][0]['articleLinks']) == 2
    assert response['markets'][0]['articleLinks'][1]['publisherName'] == '연합뉴스'
    assert 'articleLinks' not in response


def test_daily_page_assembler_preserves_display_order(sample_daily_page_payload):
    response = jsonable(assemble_daily_page_response(sample_daily_page_payload))

    assert [market['marketType'] for market in response['markets']] == ['US', 'KR']
    assert response['markets'][1]['indices'][0]['indexCode'] == 'KS11'
    assert (
        response['markets'][1]['topClusters'][0]['title'] == '반도체와 자동차 동반 강세'
    )


def test_daily_page_assembler_normalizes_utc_timestamps_to_z(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
):
    payload = pages_assembler_module.build_daily_page_payload(
        {**sample_page_snapshot_row, 'is_latest': True},
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
    )

    assert payload['generatedAt'] == '2026-03-18T06:12:10Z'
    assert payload['metadata']['lastUpdatedAt'] == '2026-03-18T06:20:00Z'
    assert payload['metadata']['isLatest'] is True
    assert payload['markets'][0]['metadata']['lastUpdatedAt'] == '2026-03-18T06:20:00Z'


def test_daily_page_assembler_keeps_legacy_session_snapshot_nullable(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
):
    payload = pages_assembler_module.build_daily_page_payload(
        sample_page_snapshot_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
    )

    market_metadata = payload['markets'][0]['metadata']
    assert market_metadata['sourceDate'] is None
    assert market_metadata['expectedSessionDate'] is None
    assert market_metadata['sessionCloseAt'] is None
    assert market_metadata['newsWindowStartAt'] is None
    assert market_metadata['newsWindowEndAt'] is None
    assert market_metadata['coverageComplete'] is None
    assert payload['markets'][0]['indices'][0]['sourceDate'] is None


def test_daily_page_assembler_exposes_market_session_snapshot(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
):
    markets = [
        {
            **row,
            'expected_session_date': date(2026, 3, 17),
            'actual_index_source_date': date(2026, 3, 17),
            'session_close_at': datetime(2026, 3, 17, 20, 0, tzinfo=UTC),
            'news_window_start_at': datetime(2026, 3, 16, 22, 0, tzinfo=UTC),
            'news_window_end_at': datetime(2026, 3, 17, 22, 0, tzinfo=UTC),
            'news_coverage_complete': True,
        }
        for row in sample_page_market_rows
    ]
    indices = [
        {
            **row,
            'source_date': date(2026, 3, 17),
            'expected_session_date': date(2026, 3, 17),
            'session_close_at': datetime(2026, 3, 17, 20, 0, tzinfo=UTC),
        }
        for row in sample_page_index_rows
    ]

    payload = pages_assembler_module.build_daily_page_payload(
        sample_page_snapshot_row,
        markets,
        indices,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
    )

    metadata = payload['markets'][0]['metadata']
    assert metadata['sourceDate'] == '2026-03-17'
    assert metadata['expectedSessionDate'] == '2026-03-17'
    assert metadata['sessionCloseAt'] == '2026-03-17T20:00:00Z'
    assert metadata['newsWindowStartAt'] == '2026-03-16T22:00:00Z'
    assert metadata['newsWindowEndAt'] == '2026-03-17T22:00:00Z'
    assert metadata['coverageComplete'] is True
    assert payload['markets'][0]['indices'][0]['sourceDate'] == '2026-03-17'
