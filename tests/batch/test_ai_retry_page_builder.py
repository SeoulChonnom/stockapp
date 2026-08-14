from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.batch.ai_retry.models import AiRetryCounts
from app.batch.ai_retry.page_builder import AiRetryPageBuilder
from app.db.repositories.projections import AiSummaryRecord

BUSINESS_DATE = date(2026, 7, 28)
GENERATED_AT = datetime(2026, 7, 29, tzinfo=UTC)
CLUSTER_UID = UUID('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa')
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
        'text': '반도체 강세가 상승을 이끌었습니다.',
    },
    {
        'kind': 'watch',
        'label': '관전 포인트',
        'text': '다음 물가 지표를 확인해야 합니다.',
    },
]
KEY_POINT_ISSUE = {
    'category': 'AI_SUMMARY',
    'code': 'KEY_POINTS_GENERATION_FAILED',
    'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
}


class FakeSourcePageRepository:
    def __init__(self, state: dict):
        self.state = state

    async def get_page_header_by_id(self, page_id):
        assert page_id == 501
        return self.state['page']

    async def get_page_markets(self, page_id):
        assert page_id == 501
        return self.state['markets']

    async def get_page_indices(self, market_ids):
        assert market_ids == [601]
        return self.state['indices']

    async def get_page_clusters(self, market_ids):
        assert market_ids == [601]
        return self.state['clusters']

    async def get_page_article_links(self, market_ids):
        assert market_ids == [601]
        return self.state['links']

    async def get_page_cluster_themes(self, page_market_cluster_ids):
        assert page_market_cluster_ids == [801]
        return self.state['themes']


class FakePageWriteRepository:
    def __init__(self, *, theme_error: Exception | None = None) -> None:
        self.page: dict | None = None
        self.markets: list[dict] = []
        self.indices: list[dict] = []
        self.clusters: list[dict] = []
        self.themes: list[tuple[int, list[dict]]] = []
        self.links: list[dict] = []
        self.next_cluster_id = 9001
        self.theme_error = theme_error

    async def get_next_version_no(self, business_date):
        assert business_date == BUSINESS_DATE
        return 4

    async def create_page(self, **kwargs):
        self.page = kwargs
        return 777

    async def create_page_market(self, **kwargs):
        self.markets.append(kwargs)
        return 888

    async def insert_page_market_index(self, payload):
        self.indices.append(payload)

    async def insert_page_market_cluster(self, payload):
        self.clusters.append(payload)
        cluster_id = self.next_cluster_id
        self.next_cluster_id += 1
        return cluster_id

    async def insert_page_market_cluster_themes(self, page_cluster_id, themes):
        if self.theme_error is not None:
            raise self.theme_error
        self.themes.append((page_cluster_id, themes))

    async def insert_page_article_link(self, payload):
        self.links.append(payload)


def _source_state(*, non_ai_issue: bool = False) -> dict:
    issues = [
        {
            'category': 'AI_SUMMARY',
            'code': 'AI_SUMMARY_FALLBACK',
            'message': 'old fallback',
        }
    ]
    if non_ai_issue:
        issues.append(
            {
                'category': 'BATCH_WARNING',
                'code': 'INDEX_STALE',
                'message': 'index data is stale',
            }
        )
    return {
        'page': {
            'id': 501,
            'business_date': BUSINESS_DATE,
            'version_no': 3,
            'page_title': 'source page',
            'status': 'PARTIAL',
            'global_headline': 'old global',
            'search_document': 'source page search document 그대로',
            'partial_message': 'AI summary fallback',
            'raw_news_count': 10,
            'processed_news_count': 8,
            'cluster_count': 1,
            'metadata_json': {'issues': issues},
        },
        'markets': [
            {
                'id': 601,
                'page_id': 501,
                'market_type': 'US',
                'expected_session_date': date(2026, 7, 24),
                'actual_index_source_date': date(2026, 7, 24),
                'session_close_at': datetime(2026, 7, 24, 20, 0, tzinfo=UTC),
                'news_window_start_at': datetime(2026, 7, 27, 22, 0, tzinfo=UTC),
                'news_window_end_at': datetime(2026, 7, 28, 22, 0, tzinfo=UTC),
                'news_coverage_complete': True,
                'display_order': 1,
                'market_label': 'US',
                'summary_title': 'old market title',
                'summary_body': 'old market body',
                'analysis_background_json': ['old background'],
                'analysis_key_themes_json': ['old theme'],
                'analysis_outlook': 'old outlook',
                'raw_news_count': 10,
                'processed_news_count': 8,
                'cluster_count': 1,
                'partial_message': None,
                'metadata_json': {},
                'search_document': 'stored market search document',
            }
        ],
        'indices': [
            {
                'id': 701,
                'page_market_id': 601,
                'market_index_daily_id': 1,
                'display_order': 1,
                'index_code': 'IXIC',
                'index_name': 'NASDAQ',
                'close_price': Decimal('1'),
                'change_value': Decimal('0.1'),
                'change_percent': Decimal('1'),
                'high_price': Decimal('2'),
                'low_price': Decimal('0.5'),
                'currency_code': 'USD',
            }
        ],
        'clusters': [
            {
                'id': 801,
                'page_market_id': 601,
                'cluster_id': 7,
                'cluster_uid': CLUSTER_UID,
                'display_order': 1,
                'title': 'cluster',
                'summary': 'old card',
                'article_count': 1,
                'tags_json': ['AI'],
                'representative_article_id': 9,
                'representative_title': 'article',
                'representative_publisher_name': 'publisher',
                'representative_published_at': GENERATED_AT,
                'representative_origin_link': 'https://example.com/a',
                'representative_naver_link': None,
                'search_document': 'stored cluster search document',
            }
        ],
        'links': [
            {
                'id': 901,
                'page_market_id': 601,
                'display_order': 1,
                'processed_article_id': 9,
                'cluster_id': 7,
                'cluster_uid': CLUSTER_UID,
                'cluster_title': 'cluster',
                'title': 'article',
                'publisher_name': 'publisher',
                'published_at': GENERATED_AT,
                'origin_link': 'https://example.com/a',
                'naver_link': None,
            }
        ],
        'themes': [
            {
                'page_market_cluster_id': 801,
                'theme_code': 'THEME_STORED_A',
                'rank': 1,
            },
            {
                'page_market_cluster_id': 801,
                'theme_code': 'THEME_STORED_C',
                'rank': 3,
            },
        ],
    }


def _summary(
    summary_id: int,
    *,
    job_id: int,
    target_key: str,
    success: bool,
    source_summary_id: int | None = None,
    metadata_json: dict | None = None,
) -> AiSummaryRecord:
    summary_type, _, suffix = target_key.partition(':')
    return AiSummaryRecord(
        summary_id=summary_id,
        batch_job_id=job_id,
        summary_type=summary_type,
        business_date=BUSINESS_DATE,
        market_type=suffix if summary_type == 'MARKET_SUMMARY' else None,
        cluster_id=int(suffix) if summary_type.startswith('CLUSTER_') else None,
        title=f'new title {target_key}',
        body=f'new body {target_key}',
        paragraphs_json=[],
        model_name='gemini',
        prompt_version='v1',
        status='SUCCESS' if success else 'FALLBACK',
        fallback_used=not success,
        error_message=None,
        metadata_json=(
            metadata_json
            if metadata_json is not None
            else {
                'background': ['new background'],
                'keyThemes': ['new theme'],
                'outlook': 'new outlook',
            }
        ),
        generated_at=GENERATED_AT,
        target_key=target_key,
        source_summary_id=source_summary_id,
        attempt_no=2 if source_summary_id else 1,
    )


def _lineage(*, recover_market: bool, recover_all: bool) -> list[AiSummaryRecord]:
    targets = [
        'GLOBAL_HEADLINE',
        'MARKET_SUMMARY:US',
        'CLUSTER_CARD_SUMMARY:7',
    ]
    source = [
        _summary(index, job_id=10, target_key=target, success=False)
        for index, target in enumerate(targets, start=1)
    ]
    retry_targets = targets if recover_all else targets[: 2 if recover_market else 1]
    retry = [
        _summary(
            index + 10,
            job_id=20,
            target_key=target,
            success=True,
            source_summary_id=index,
        )
        for index, target in enumerate(retry_targets, start=1)
    ]
    return [*source, *retry]


@pytest.mark.anyio
async def test_all_recovery_creates_ready_vnext_with_ai_overlay_and_cloned_links():
    source_state = _source_state()
    original_state = deepcopy(source_state)
    writes = FakePageWriteRepository()
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=_lineage(recover_market=True, recover_all=True),
        counts=AiRetryCounts(
            target_count=3,
            attempted_count=3,
            success_count=3,
            recovered_count=3,
        ),
    )

    assert result.status == 'READY'
    assert result.version_no == 4
    assert writes.page is not None
    assert writes.page['global_headline'] == 'new title GLOBAL_HEADLINE'
    assert writes.page['search_document'] == 'source page search document 그대로'
    assert writes.markets[0]['summary_body'] == 'new body MARKET_SUMMARY:US'
    assert writes.markets[0]['expected_session_date'] == date(2026, 7, 24)
    assert writes.markets[0]['actual_index_source_date'] == date(2026, 7, 24)
    assert writes.markets[0]['session_close_at'] == datetime(
        2026, 7, 24, 20, 0, tzinfo=UTC
    )
    assert writes.markets[0]['news_window_start_at'] == datetime(
        2026, 7, 27, 22, 0, tzinfo=UTC
    )
    assert writes.markets[0]['news_window_end_at'] == datetime(
        2026, 7, 28, 22, 0, tzinfo=UTC
    )
    assert writes.markets[0]['news_coverage_complete'] is True
    assert writes.clusters[0]['summary'] == ('new body CLUSTER_CARD_SUMMARY:7')
    assert writes.themes == [
        (
            9001,
            [
                {'theme_code': 'THEME_STORED_A', 'rank': 1},
                {'theme_code': 'THEME_STORED_C', 'rank': 3},
            ],
        )
    ]
    assert writes.indices[0]['index_code'] == 'IXIC'
    assert writes.links[0]['origin_link'] == 'https://example.com/a'
    assert source_state == original_state


@pytest.mark.anyio
async def test_successful_headline_key_point_issue_keeps_retry_page_partial():
    source_state = _source_state()
    writes = FakePageWriteRepository()
    lineage = _lineage(recover_market=True, recover_all=True)
    lineage = [
        (
            replace(
                summary,
                metadata_json={
                    'reason': 'llm',
                    'keyPoints': [],
                    'keyPointIssue': KEY_POINT_ISSUE,
                    'retry': {'sourceSummaryId': 1, 'attemptNo': 2},
                },
            )
            if summary.batch_job_id == 20 and summary.target_key == 'GLOBAL_HEADLINE'
            else summary
        )
        for summary in lineage
    ]
    global_retry = next(
        summary
        for summary in lineage
        if summary.batch_job_id == 20 and summary.target_key == 'GLOBAL_HEADLINE'
    )
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=lineage,
        counts=AiRetryCounts(
            target_count=3,
            attempted_count=3,
            success_count=3,
            recovered_count=3,
        ),
    )

    assert global_retry.status == 'SUCCESS'
    assert global_retry.fallback_used is False
    assert result.status == 'PARTIAL'
    assert result.partial_message == KEY_POINT_ISSUE['message']
    assert writes.page is not None
    assert writes.page['status'] == 'PARTIAL'
    assert writes.page['metadata_json']['keyPoints'] == []
    assert writes.page['metadata_json']['issues'] == [KEY_POINT_ISSUE]


@pytest.mark.anyio
async def test_retry_page_preserves_key_points_when_headline_remains_fallback():
    source_state = _source_state()
    writes = FakePageWriteRepository()
    source_key_points = deepcopy(KEY_POINTS)
    lineage = _lineage(recover_market=True, recover_all=False)
    lineage = [
        (
            replace(
                summary,
                status='FALLBACK',
                fallback_used=True,
                metadata_json={
                    'reason': 'llm_fallback',
                    'keyPoints': source_key_points,
                    'keyPointIssue': None,
                    'retry': {'sourceSummaryId': 1, 'attemptNo': 2},
                },
            )
            if summary.batch_job_id == 20 and summary.target_key == 'GLOBAL_HEADLINE'
            else summary
        )
        for summary in lineage
    ]
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=lineage,
        counts=AiRetryCounts(
            target_count=3,
            attempted_count=3,
            success_count=1,
            fallback_count=2,
            recovered_count=1,
        ),
    )

    assert result.status == 'PARTIAL'
    assert writes.page is not None
    assert writes.page['metadata_json']['keyPoints'] == KEY_POINTS
    assert all(
        issue['code'] != 'KEY_POINTS_GENERATION_FAILED'
        for issue in writes.page['metadata_json']['issues']
    )
    writes.page['metadata_json']['keyPoints'][0]['text'] = '새 페이지 변경'
    assert source_key_points == KEY_POINTS


@pytest.mark.anyio
async def test_partial_recovery_creates_partial_vnext():
    source_state = _source_state()
    writes = FakePageWriteRepository()
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=_lineage(recover_market=False, recover_all=False),
        counts=AiRetryCounts(
            target_count=3,
            attempted_count=3,
            success_count=1,
            fallback_count=2,
            recovered_count=1,
        ),
    )

    assert result.status == 'PARTIAL'
    assert 'remain' in (result.partial_message or '')
    assert writes.page is not None
    assert len(writes.page['metadata_json']['issues']) == 2
    assert writes.markets[0]['search_document'] == 'stored market search document'
    assert writes.clusters[0]['search_document'] == 'stored cluster search document'


@pytest.mark.anyio
async def test_retry_theme_write_failure_propagates_without_filling_from_mutable_source():
    source_state = _source_state()
    writes = FakePageWriteRepository(theme_error=RuntimeError('theme write failed'))
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    with pytest.raises(RuntimeError, match='theme write failed'):
        await builder.build(
            session=object(),
            source_page_id=501,
            source_job_id=10,
            retry_job_id=20,
            summaries=_lineage(recover_market=False, recover_all=False),
            counts=AiRetryCounts(
                target_count=3,
                attempted_count=3,
                success_count=1,
                fallback_count=2,
                recovered_count=1,
            ),
        )

    assert writes.page is not None
    assert writes.markets[0]['search_document'] == 'stored market search document'
    assert writes.clusters[0]['search_document'] == 'stored cluster search document'
    assert writes.themes == []


@pytest.mark.anyio
async def test_unavailable_cluster_detail_does_not_degrade_retry_page():
    source_state = _source_state()
    writes = FakePageWriteRepository()
    global_source = _summary(
        1,
        job_id=10,
        target_key='GLOBAL_HEADLINE',
        success=False,
    )
    detail_source = _summary(
        2,
        job_id=10,
        target_key='CLUSTER_DETAIL_ANALYSIS:7',
        success=False,
        metadata_json={
            'analysisStatus': 'UNAVAILABLE',
            'analysisIssues': [
                {
                    'code': 'ANALYSIS_GENERATION_FAILED',
                    'message': '분석을 생성하지 못했습니다.',
                }
            ],
            'conflictStatus': 'NOT_CHECKED',
        },
    )
    global_retry = _summary(
        11,
        job_id=20,
        target_key='GLOBAL_HEADLINE',
        success=True,
        source_summary_id=1,
        metadata_json={'keyPoints': KEY_POINTS, 'keyPointIssue': None},
    )
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=[global_source, detail_source, global_retry],
        counts=AiRetryCounts(
            target_count=2,
            attempted_count=2,
            success_count=1,
            fallback_count=1,
            recovered_count=1,
        ),
    )

    assert detail_source.status == 'FALLBACK'
    assert detail_source.fallback_used is True
    assert result.status == 'READY'
    assert result.partial_message is None
    assert writes.page is not None
    assert writes.page['status'] == 'READY'
    assert writes.page['metadata_json']['issues'] == []


@pytest.mark.anyio
async def test_non_ai_issue_keeps_all_recovery_page_partial():
    source_state = _source_state(non_ai_issue=True)
    writes = FakePageWriteRepository()
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(source_state),
        snapshot_repo_factory=lambda _: writes,
    )

    result = await builder.build(
        session=object(),
        source_page_id=501,
        source_job_id=10,
        retry_job_id=20,
        summaries=_lineage(recover_market=True, recover_all=True),
        counts=AiRetryCounts(
            target_count=3,
            attempted_count=3,
            success_count=3,
            recovered_count=3,
        ),
    )

    assert result.status == 'PARTIAL'
    assert result.partial_message == 'index data is stale'


@pytest.mark.anyio
async def test_no_recovery_does_not_create_page():
    writes = FakePageWriteRepository()
    builder = AiRetryPageBuilder(
        source_page_repo_factory=lambda _: FakeSourcePageRepository(_source_state()),
        snapshot_repo_factory=lambda _: writes,
    )

    with pytest.raises(ValueError, match='at least one recovered'):
        await builder.build(
            session=object(),
            source_page_id=501,
            source_job_id=10,
            retry_job_id=20,
            summaries=_lineage(recover_market=False, recover_all=False),
            counts=AiRetryCounts(target_count=3),
        )

    assert writes.page is None
