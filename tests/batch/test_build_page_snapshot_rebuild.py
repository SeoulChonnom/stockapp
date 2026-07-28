from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest

from tests.market_context_fakes import CompleteMarketContextRepository
from tests.support import RecordingAsyncSession, load_module

batch_models_module = load_module('app.batch.models')
build_module = load_module('app.batch.steps.build_page_snapshot')

BatchExecutionContext = batch_models_module.BatchExecutionContext
BuildPageSnapshotStep = build_module.BuildPageSnapshotStep


@dataclass
class EventRepository:
    session: RecordingAsyncSession
    events: list[dict]

    async def add_event(self, **kwargs):
        self.events.append(kwargs)


class SourceClusterRepository:
    def __init__(self, session):
        _ = session

    async def list_clusters_by_business_date(self, business_date):
        _ = business_date
        return [
            {
                'id': 7001,
                'cluster_uid': 'cluster-uid',
                'market_type': 'US',
                'title': '기존 클러스터',
                'summary_short': '기존 클러스터 요약',
                'tags_json': [],
                'representative_article_id': 4001,
                'article_count': 2,
            }
        ]

    async def list_cluster_article_links_by_business_date(self, business_date):
        _ = business_date
        return []


class EmptyIndexRepository:
    def __init__(self, session):
        _ = session

    async def list_indices_by_business_date(self, business_date):
        _ = business_date
        return []


class FailingLiveRepository:
    def __init__(self, session):
        raise AssertionError(
            f'rebuild must not construct live repository for {session!r}'
        )


class DifferentLiveRepository:
    def __init__(self):
        self.calls: list[str] = []

    async def list_clusters_by_business_date(self, business_date):
        _ = business_date
        self.calls.append('clusters')
        return [{'id': 9999, 'title': '게시되지 않은 라이브 클러스터'}]

    async def list_cluster_article_links_by_business_date(self, business_date):
        _ = business_date
        self.calls.append('article_links')
        return [{'title': '게시되지 않은 라이브 기사'}]

    async def list_indices_by_business_date(self, business_date):
        _ = business_date
        self.calls.append('indices')
        return [{'index_code': '^LIVE'}]

    async def list_summaries_for_job(self, job_id):
        _ = job_id
        self.calls.append('summaries')
        return []


class ExistingPageRepository:
    def __init__(self, session):
        _ = session

    async def get_page_header_by_business_date(self, business_date):
        _ = business_date
        return {
            'id': 501,
            'page_title': '저장된 페이지 제목',
            'status': 'PARTIAL',
            'global_headline': '저장된 글로벌 헤드라인',
            'partial_message': '기존 부분 생성 사유',
            'raw_news_count': 10,
            'processed_news_count': 6,
            'cluster_count': 1,
            'metadata_json': {
                'warnings': ['기존 경고'],
                'sourceMarker': 'stored-page',
            },
        }

    async def get_page_markets(self, page_id):
        assert page_id == 501
        return [
            {
                'id': 901,
                'page_id': 501,
                'market_type': 'US',
                'display_order': 1,
                'market_label': '저장된 미국 시장',
                'summary_title': '저장된 시장 요약',
                'summary_body': '저장된 시장 본문',
                'analysis_background_json': ['저장된 배경'],
                'analysis_key_themes_json': ['저장된 테마'],
                'analysis_outlook': '저장된 전망',
                'raw_news_count': 7,
                'processed_news_count': 4,
                'cluster_count': 1,
                'partial_message': '저장된 시장 부분 사유',
                'metadata_json': {'sourceMarker': 'stored-market'},
            }
        ]

    async def get_page_indices(self, page_market_ids):
        assert page_market_ids == [901]
        return [
            {
                'id': 801,
                'page_market_id': 901,
                'market_index_daily_id': 3001,
                'display_order': 1,
                'index_code': '^STORED',
                'index_name': '저장된 지수',
                'close_price': 101,
                'change_value': 2,
                'change_percent': 2.02,
                'high_price': 103,
                'low_price': 99,
                'currency_code': 'USD',
            }
        ]

    async def get_page_clusters(self, page_market_ids):
        assert page_market_ids == [901]
        return [
            {
                'id': 701,
                'page_market_id': 901,
                'cluster_id': 7001,
                'cluster_uid': 'stored-cluster-uid',
                'display_order': 1,
                'title': '저장된 클러스터',
                'summary': '저장된 클러스터 요약',
                'article_count': 2,
                'tags_json': ['저장됨'],
                'representative_article_id': 4001,
                'representative_title': '저장된 대표 기사',
                'representative_publisher_name': '저장 매체',
                'representative_published_at': None,
                'representative_origin_link': 'https://stored.example/article',
                'representative_naver_link': None,
            }
        ]

    async def get_page_article_links(self, page_market_ids):
        assert page_market_ids == [901]
        return [
            {
                'id': 601,
                'page_market_id': 901,
                'display_order': 1,
                'processed_article_id': 4001,
                'cluster_id': 7001,
                'cluster_uid': 'stored-cluster-uid',
                'cluster_title': '저장된 클러스터',
                'title': '저장된 기사',
                'publisher_name': '저장 매체',
                'published_at': None,
                'origin_link': 'https://stored.example/article',
                'naver_link': None,
            }
        ]


class EmptyStoredPageRepository(ExistingPageRepository):
    async def get_page_markets(self, page_id):
        _ = page_id
        return []


class QueuedStoredPageRepository(ExistingPageRepository):
    async def get_page_header_by_id(self, page_id):
        assert page_id == 501
        return await super().get_page_header_by_business_date(date(2026, 3, 17))

    async def get_page_header_by_business_date(self, business_date):
        raise AssertionError(
            f'queued rebuild must not reselect latest page for {business_date}'
        )


class RecordingSnapshotRepository:
    def __init__(self, session):
        _ = session
        self.calls: list[tuple[str, dict]] = []

    async def get_next_version_no(self, business_date):
        _ = business_date
        return 4

    async def create_page(self, **kwargs):
        self.calls.append(('create_page', kwargs))
        return 502

    async def create_page_market(self, **kwargs):
        self.calls.append(('create_page_market', kwargs))
        return 1001

    async def insert_page_market_index(self, params):
        self.calls.append(('insert_page_market_index', params))

    async def insert_page_market_cluster(self, params):
        self.calls.append(('insert_page_market_cluster', params))

    async def insert_page_article_link(self, params):
        self.calls.append(('insert_page_article_link', params))


@pytest.mark.anyio
async def test_rebuild_uses_persisted_source_and_preserves_page_outcome():
    live_repository = DifferentLiveRepository()
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=lambda session: live_repository,
        summary_repo_factory=lambda session: live_repository,
        index_repo_factory=lambda session: live_repository,
        source_page_repo_factory=ExistingPageRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
    )
    context = BatchExecutionContext(
        job_id=2002,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=True,
    )

    updated_context = await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    assert live_repository.calls == []
    assert updated_context.page_id == 502
    assert updated_context.page_version_no == 4
    assert updated_context.raw_news_count == 10
    assert updated_context.processed_news_count == 6
    assert updated_context.cluster_count == 1
    assert updated_context.partial_message == '기존 부분 생성 사유'
    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert create_page == {
        'business_date': date(2026, 3, 17),
        'version_no': 4,
        'page_title': '저장된 페이지 제목',
        'status': 'PARTIAL',
        'global_headline': '저장된 글로벌 헤드라인',
        'partial_message': '기존 부분 생성 사유',
        'raw_news_count': 10,
        'processed_news_count': 6,
        'cluster_count': 1,
        'batch_job_id': 2002,
        'metadata_json': {
            'warnings': ['기존 경고'],
            'sourceMarker': 'stored-page',
        },
    }
    create_market = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'create_page_market'
    )
    assert create_market == {
        'page_id': 502,
        'market_type': 'US',
        'display_order': 1,
        'market_label': '저장된 미국 시장',
        'summary_title': '저장된 시장 요약',
        'summary_body': '저장된 시장 본문',
        'analysis_background_json': ['저장된 배경'],
        'analysis_key_themes_json': ['저장된 테마'],
        'analysis_outlook': '저장된 전망',
        'raw_news_count': 7,
        'processed_news_count': 4,
        'cluster_count': 1,
        'partial_message': '저장된 시장 부분 사유',
        'metadata_json': {'sourceMarker': 'stored-market'},
    }
    index_row = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'insert_page_market_index'
    )
    assert index_row == {
        'page_market_id': 1001,
        'market_index_daily_id': 3001,
        'display_order': 1,
        'index_code': '^STORED',
        'index_name': '저장된 지수',
        'close_price': 101,
        'change_value': 2,
        'change_percent': 2.02,
        'high_price': 103,
        'low_price': 99,
        'currency_code': 'USD',
    }
    cluster_row = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'insert_page_market_cluster'
    )
    assert cluster_row == {
        'page_market_id': 1001,
        'cluster_id': 7001,
        'cluster_uid': 'stored-cluster-uid',
        'display_order': 1,
        'title': '저장된 클러스터',
        'summary': '저장된 클러스터 요약',
        'article_count': 2,
        'tags_json': ['저장됨'],
        'representative_article_id': 4001,
        'representative_title': '저장된 대표 기사',
        'representative_publisher_name': '저장 매체',
        'representative_published_at': None,
        'representative_origin_link': 'https://stored.example/article',
        'representative_naver_link': None,
    }
    article_row = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'insert_page_article_link'
    )
    assert article_row == {
        'page_market_id': 1001,
        'display_order': 1,
        'processed_article_id': 4001,
        'cluster_id': 7001,
        'cluster_uid': 'stored-cluster-uid',
        'cluster_title': '저장된 클러스터',
        'title': '저장된 기사',
        'publisher_name': '저장 매체',
        'published_at': None,
        'origin_link': 'https://stored.example/article',
        'naver_link': None,
    }


@pytest.mark.anyio
async def test_queued_rebuild_uses_captured_source_page_id():
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=FailingLiveRepository,
        summary_repo_factory=FailingLiveRepository,
        index_repo_factory=FailingLiveRepository,
        source_page_repo_factory=QueuedStoredPageRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
    )
    context = BatchExecutionContext(
        job_id=2002,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=True,
        source_job_id=1001,
        source_page_id=501,
    )

    updated_context = await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    assert updated_context.page_id == 502
    assert updated_context.source_job_id == 1001
    assert updated_context.source_page_id == 501


@pytest.mark.anyio
async def test_rebuild_rejects_when_persisted_page_children_are_missing():
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=FailingLiveRepository,
        summary_repo_factory=FailingLiveRepository,
        index_repo_factory=FailingLiveRepository,
        source_page_repo_factory=EmptyStoredPageRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
    )
    context = BatchExecutionContext(
        job_id=2002,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=True,
    )

    updated_context = await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    assert updated_context.error_code == 'SNAPSHOT_SOURCE_MISSING'
    assert updated_context.page_id is None
    assert snapshot_repository.calls == []


@pytest.mark.anyio
async def test_normal_snapshot_marks_fallback_partial_and_builds_partial_message():
    class EmptySummaryRepository:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return []

    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=SourceClusterRepository,
        summary_repo_factory=EmptySummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    )
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
        fallback_count=1,
        partial_reasons=['요약 일부가 대체 생성되었습니다.'],
        warning_messages=['외부 제공자 경고'],
    )

    updated_context = await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert create_page['status'] == 'PARTIAL'
    assert (
        create_page['partial_message']
        == '요약 일부가 대체 생성되었습니다.; 외부 제공자 경고'
    )
    assert updated_context.partial_message == create_page['partial_message']
