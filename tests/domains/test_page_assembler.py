from __future__ import annotations

from copy import deepcopy
from datetime import UTC, date, datetime

import pytest  # pyright: ignore[reportMissingImports]
from pydantic import ValidationError

from tests.support import jsonable, load_module

pages_assembler_module = load_module('app.domains.pages.assembler')

assemble_daily_page_response = pages_assembler_module.assemble_daily_page_response
build_daily_page_payload = pages_assembler_module.build_daily_page_payload


KEY_POINTS = [
    {
        'kind': 'direction',
        'label': '시장 방향',
        'text': '주요 지수가 상승했습니다.',
        'direction': 'UP',
    },
    {
        'kind': 'driver',
        'label': '주요 원인',
        'text': '반도체 업종 강세가 상승을 이끌었습니다.',
    },
    {
        'kind': 'watch',
        'label': '관전 포인트',
        'text': '다음 거래일 금리 발표를 확인해야 합니다.',
    },
]


def test_daily_page_requires_exact_key_point_success_shape(
    sample_daily_page_payload,
):
    payload = {**sample_daily_page_payload, 'keyPoints': KEY_POINTS}

    response = jsonable(assemble_daily_page_response(payload))

    assert response['keyPoints'] == KEY_POINTS


@pytest.mark.parametrize('direction', ['UP', 'DOWN', 'MIXED', 'FLAT'])
def test_daily_page_accepts_approved_direction_values(
    direction,
    sample_daily_page_payload,
):
    key_points = deepcopy(KEY_POINTS)
    key_points[0]['direction'] = direction

    response = jsonable(
        assemble_daily_page_response(
            {**sample_daily_page_payload, 'keyPoints': key_points}
        )
    )

    assert response['keyPoints'][0]['direction'] == direction


def test_daily_page_rejects_unapproved_direction(sample_daily_page_payload):
    key_points = deepcopy(KEY_POINTS)
    key_points[0]['direction'] = 'SIDEWAYS'

    with pytest.raises(ValidationError):
        assemble_daily_page_response(
            {**sample_daily_page_payload, 'keyPoints': key_points}
        )


def test_daily_page_rejects_noncanonical_key_point_label(
    sample_daily_page_payload,
):
    key_points = deepcopy(KEY_POINTS)
    key_points[1]['label'] = '원인'

    with pytest.raises(ValidationError):
        assemble_daily_page_response(
            {**sample_daily_page_payload, 'keyPoints': key_points}
        )


@pytest.mark.parametrize('index', [1, 2], ids=['driver', 'watch'])
def test_daily_page_rejects_direction_on_non_direction_key_point(
    index,
    sample_daily_page_payload,
):
    key_points = deepcopy(KEY_POINTS)
    key_points[index]['direction'] = 'UP'

    with pytest.raises(ValidationError):
        assemble_daily_page_response(
            {**sample_daily_page_payload, 'keyPoints': key_points}
        )


@pytest.mark.parametrize(
    'key_points',
    [None, KEY_POINTS[:2], [KEY_POINTS[1], KEY_POINTS[0], KEY_POINTS[2]]],
    ids=['null', 'partial', 'wrong-order'],
)
def test_daily_page_rejects_invalid_key_point_collection(
    key_points,
    sample_daily_page_payload,
):
    with pytest.raises(ValidationError):
        assemble_daily_page_response(
            {**sample_daily_page_payload, 'keyPoints': key_points}
        )


def test_daily_page_rejects_omitted_key_points(sample_daily_page_payload):
    payload = deepcopy(sample_daily_page_payload)
    del payload['keyPoints']

    with pytest.raises(ValidationError):
        assemble_daily_page_response(payload)


def test_daily_page_represents_key_point_failure_as_empty_array(
    sample_daily_page_payload,
):
    response = jsonable(
        assemble_daily_page_response({**sample_daily_page_payload, 'keyPoints': []})
    )

    assert response['keyPoints'] == []


def test_daily_page_article_grouping_placeholders_are_truthful(
    sample_daily_page_payload,
):
    payload = {**sample_daily_page_payload, 'keyPoints': []}

    response = jsonable(assemble_daily_page_response(payload))

    article = response['markets'][0]['articleLinks'][0]
    assert article['processedArticleId'] == 2001
    assert article['similarGroupId'] == 'sim-51f0d9a0-9fc5-4f15-a4f9-62856f128683-1'
    assert article['isSimilarGroupRepresentative'] is True
    assert article['exactDuplicateCount'] == 0


@pytest.mark.parametrize(
    'field',
    ['similarGroupId', 'isSimilarGroupRepresentative', 'exactDuplicateCount'],
)
def test_daily_page_article_grouping_fields_are_required(
    field,
    sample_daily_page_payload,
):
    payload = deepcopy(sample_daily_page_payload)
    del payload['markets'][0]['articleLinks'][0][field]

    with pytest.raises(ValidationError):
        assemble_daily_page_response(payload)


@pytest.mark.parametrize('processed_article_id', ['missing', None])
def test_daily_page_article_links_require_integer_processed_article_id(
    processed_article_id,
    sample_daily_page_payload,
):
    payload = deepcopy(sample_daily_page_payload)
    payload['keyPoints'] = []
    article = payload['markets'][0]['articleLinks'][0]
    if processed_article_id == 'missing':
        del article['processedArticleId']
    else:
        article['processedArticleId'] = processed_article_id

    with pytest.raises(ValidationError):
        assemble_daily_page_response(payload)


def test_daily_page_assembler_keeps_market_nested_article_links(
    sample_daily_page_payload,
):
    response = jsonable(
        assemble_daily_page_response({**sample_daily_page_payload, 'keyPoints': []})
    )

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
    response = jsonable(
        assemble_daily_page_response({**sample_daily_page_payload, 'keyPoints': []})
    )

    assert [market['marketType'] for market in response['markets']] == ['US', 'KR']
    assert response['markets'][1]['indices'][0]['indexCode'] == 'KS11'
    assert (
        response['markets'][1]['topClusters'][0]['title'] == '반도체와 자동차 동반 강세'
    )


def test_daily_page_assembler_redacts_legacy_provider_diagnostics(
    sample_daily_page_payload,
):
    payload = deepcopy(sample_daily_page_payload)
    naver_reason = (
        'Naver news pagination cap was reached before covering the persisted '
        "window for keyword '증시'."
    )
    raw_provider_reason = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    payload['partialMessage'] = f'{naver_reason}; {raw_provider_reason}'
    payload['markets'][0]['metadata']['partialMessage'] = raw_provider_reason
    payload['keyPoints'] = []

    response = jsonable(assemble_daily_page_response(payload))
    serialized = repr(response)

    assert naver_reason in response['partialMessage']
    assert (
        'AI provider request failed; fallback content was used.'
        in response['partialMessage']
    )
    assert '429' not in serialized
    assert 'RetryInfo' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized


def test_daily_page_assembler_normalizes_utc_timestamps_to_z(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    payload = pages_assembler_module.build_daily_page_payload(
        {**sample_page_snapshot_row, 'is_latest': True},
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
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
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    payload = pages_assembler_module.build_daily_page_payload(
        sample_page_snapshot_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
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
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
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
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    metadata = payload['markets'][0]['metadata']
    assert metadata['sourceDate'] == '2026-03-17'
    assert metadata['expectedSessionDate'] == '2026-03-17'
    assert metadata['sessionCloseAt'] == '2026-03-17T20:00:00Z'
    assert metadata['newsWindowStartAt'] == '2026-03-16T22:00:00Z'
    assert metadata['newsWindowEndAt'] == '2026-03-17T22:00:00Z'
    assert metadata['coverageComplete'] is True
    assert payload['markets'][0]['indices'][0]['sourceDate'] == '2026-03-17'


def test_daily_page_assembler_exposes_ready_page_with_empty_issues(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    """The fixture page is READY with metadata_json == {}: no issues stored."""
    payload = build_daily_page_payload(
        sample_page_snapshot_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    assert payload['issues'] == []


def test_daily_page_assembler_extracts_structured_issues_from_metadata(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    page_row = {
        **sample_page_snapshot_row,
        'metadata_json': {
            'warnings': ['Naver 뉴스 페이지네이션 한도에 도달했습니다.'],
            'issues': [
                {
                    'category': 'AI_SUMMARY',
                    'code': 'AI_SUMMARY_FALLBACK',
                    'message': 'AI summary fallback for GLOBAL_HEADLINE due to a timeout.',
                },
                {
                    'category': 'BATCH_WARNING',
                    'code': 'BATCH_WARNING',
                    'message': 'Naver 뉴스 페이지네이션 한도에 도달했습니다.',
                },
            ],
        },
    }

    payload = build_daily_page_payload(
        page_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    assert payload['issues'] == [
        {
            'category': 'AI_SUMMARY',
            'code': 'AI_SUMMARY_FALLBACK',
            'message': 'AI summary fallback for GLOBAL_HEADLINE due to a timeout.',
        },
        {
            'category': 'BATCH_WARNING',
            'code': 'BATCH_WARNING',
            'message': 'Naver 뉴스 페이지네이션 한도에 도달했습니다.',
        },
    ]


@pytest.mark.parametrize(
    'metadata_json',
    [None, {}, 'not-a-dict', ['also-not-a-dict'], {'issues': 'not-a-list'}],
    ids=['none', 'empty-dict', 'string', 'list', 'issues-not-a-list'],
)
def test_daily_page_assembler_tolerates_malformed_metadata_json(
    metadata_json,
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    page_row = {**sample_page_snapshot_row, 'metadata_json': metadata_json}

    payload = build_daily_page_payload(
        page_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    assert payload['issues'] == []


def test_daily_page_assembler_drops_malformed_issue_entries(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    page_row = {
        **sample_page_snapshot_row,
        'metadata_json': {
            'issues': [
                {
                    'category': 'BATCH_WARNING',
                    'code': 'BATCH_WARNING',
                    'message': 'valid message',
                },
                'not-a-dict',
                {'category': 'BATCH_WARNING', 'code': 'BATCH_WARNING'},
                {'category': 123, 'code': 'BATCH_WARNING', 'message': 'x'},
                {'category': 'BATCH_WARNING', 'code': 'BATCH_WARNING', 'message': 42},
                None,
            ],
        },
    }

    payload = build_daily_page_payload(
        page_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    assert payload['issues'] == [
        {
            'category': 'BATCH_WARNING',
            'code': 'BATCH_WARNING',
            'message': 'valid message',
        },
    ]


def test_daily_page_assembler_sanitizes_issue_messages(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    raw_provider_reason = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    page_row = {
        **sample_page_snapshot_row,
        'metadata_json': {
            'issues': [
                {
                    'category': 'AI_SUMMARY',
                    'code': 'AI_SUMMARY_FALLBACK',
                    'message': raw_provider_reason,
                },
            ],
        },
    }

    payload = build_daily_page_payload(
        page_row,
        sample_page_market_rows,
        sample_page_index_rows,
        sample_page_cluster_rows,
        sample_page_article_link_rows,
        neighbors=sample_adjacent_business_dates_row,
        versions=sample_page_version_rows,
    )

    assert len(payload['issues']) == 1
    message = payload['issues'][0]['message']
    serialized = repr(payload['issues'])
    assert 'AI provider request failed' in message
    assert '429' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized


def test_daily_page_assembler_response_redacts_issue_messages(
    sample_daily_page_payload,
):
    payload = deepcopy(sample_daily_page_payload)
    raw_provider_reason = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    payload['issues'] = [
        {
            'category': 'AI_SUMMARY',
            'code': 'AI_SUMMARY_FALLBACK',
            'message': raw_provider_reason,
        },
    ]
    payload['keyPoints'] = []

    response = jsonable(assemble_daily_page_response(payload))
    serialized = repr(response)

    assert len(response['issues']) == 1
    assert 'AI provider request failed' in response['issues'][0]['message']
    assert '429' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized
