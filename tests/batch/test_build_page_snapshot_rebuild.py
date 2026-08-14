from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

import pytest

from tests.market_context_fakes import CompleteMarketContextRepository
from tests.support import RecordingAsyncSession, load_module

batch_models_module = load_module('app.batch.models')
build_module = load_module('app.batch.steps.build_page_snapshot')

BatchExecutionContext = batch_models_module.BatchExecutionContext
BuildPageSnapshotStep = build_module.BuildPageSnapshotStep


def test_search_document_uses_shared_nfc_casefold_and_whitespace_normalizer():
    assert (
        build_module._build_search_document(
            '  A\u0301  ',
            '제목\n본문',
            ['  배경  ', '테  마'],
            None,
        )
        == 'á 제목 본문 배경 테 마'
    )


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

    async def list_cluster_themes_by_business_date(self, business_date):
        _ = business_date
        return [{'cluster_id': 7001, 'theme_code': 'THEME_A', 'rank': 1}]


class EmptyIndexRepository:
    def __init__(self, session):
        _ = session

    async def list_indices_by_business_date(self, business_date):
        _ = business_date
        return []


class SuccessfulKeyPointSummaryRepository:
    metadata = {
        'reason': 'llm',
        'keyPoints': KEY_POINTS,
        'keyPointIssue': None,
    }

    def __init__(self, session):
        _ = session

    async def list_summaries_for_job(self, job_id):
        _ = job_id
        return [
            SimpleNamespace(
                summary_type='GLOBAL_HEADLINE',
                market_type=None,
                cluster_id=None,
                title='저장할 글로벌 헤드라인',
                metadata_json=self.metadata,
            )
        ]


class FailedKeyPointSummaryRepository(SuccessfulKeyPointSummaryRepository):
    metadata = {
        'reason': 'llm',
        'keyPoints': [],
        'keyPointIssue': KEY_POINT_ISSUE,
    }


class InvalidIdentityClusterRepository(SourceClusterRepository):
    invalid_field = 'processed_article_id'

    async def list_clusters_by_business_date(self, business_date):
        clusters = await super().list_clusters_by_business_date(business_date)
        if self.invalid_field == 'cluster_uid':
            clusters[0]['cluster_uid'] = None
        return clusters

    async def list_cluster_article_links_by_business_date(self, business_date):
        _ = business_date
        article_link = {
            'market_type': 'US',
            'processed_article_id': 4001,
            'cluster_id': 7001,
            'cluster_uid': 'cluster-uid',
            'cluster_title': '기존 클러스터',
            'title': '기존 기사',
            'origin_link': 'https://example.com/article',
        }
        article_link[self.invalid_field] = None
        return [article_link]


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
                'keyPoints': KEY_POINTS,
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
                'search_document': '저장된 시장 문서 그대로',
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
                'search_document': '저장된 클러스터 문서 그대로',
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

    async def get_page_cluster_themes(self, page_market_cluster_ids):
        assert page_market_cluster_ids == [701]
        return []


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
        self.next_snapshot_cluster_id = 1002

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
        snapshot_cluster_id = self.next_snapshot_cluster_id
        self.next_snapshot_cluster_id += 1
        return snapshot_cluster_id

    async def insert_page_market_cluster_themes(self, page_cluster_id, themes):
        self.calls.append(
            (
                'insert_page_market_cluster_themes',
                {'page_market_cluster_id': page_cluster_id, 'themes': themes},
            )
        )

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
        'search_document': '저장된 페이지 제목 저장된 글로벌 헤드라인',
        'partial_message': '기존 부분 생성 사유',
        'raw_news_count': 10,
        'processed_news_count': 6,
        'cluster_count': 1,
        'batch_job_id': 2002,
        'metadata_json': {
            'warnings': ['기존 경고'],
            'sourceMarker': 'stored-page',
            'keyPoints': KEY_POINTS,
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
        'search_document': '저장된 시장 문서 그대로',
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
        'search_document': '저장된 클러스터 문서 그대로',
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
async def test_rebuild_preserves_persisted_key_points_without_live_generation():
    source_metadata = {
        'warnings': ['기존 경고'],
        'sourceMarker': 'stored-page',
        'keyPoints': KEY_POINTS,
    }

    class SharedMetadataPageRepository(ExistingPageRepository):
        async def get_page_header_by_business_date(self, business_date):
            page = await super().get_page_header_by_business_date(business_date)
            page['metadata_json'] = source_metadata
            return page

    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=FailingLiveRepository,
        summary_repo_factory=FailingLiveRepository,
        index_repo_factory=FailingLiveRepository,
        source_page_repo_factory=SharedMetadataPageRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
    )
    context = BatchExecutionContext(
        job_id=2002,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=True,
    )

    await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert create_page['metadata_json']['keyPoints'] == KEY_POINTS
    assert create_page['metadata_json'] is not source_metadata

    create_page['metadata_json']['keyPoints'][0]['text'] = '복사본만 변경된 문장입니다.'
    assert source_metadata['keyPoints'][0]['text'] == KEY_POINTS[0]['text']
    assert KEY_POINTS[0]['text'] == '주요 지수가 상승했습니다.'


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


@pytest.mark.anyio
async def test_normal_snapshot_copies_successful_key_points_into_page_metadata():
    original_metadata = deepcopy(SuccessfulKeyPointSummaryRepository.metadata)
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    await BuildPageSnapshotStep(
        cluster_repo_factory=SourceClusterRepository,
        summary_repo_factory=SuccessfulKeyPointSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(EventRepository(session=RecordingAsyncSession(), events=[]), context)

    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert create_page['status'] == 'READY'
    assert create_page['metadata_json'] == {
        'warnings': [],
        'issues': [],
        'keyPoints': KEY_POINTS,
    }
    assert SuccessfulKeyPointSummaryRepository.metadata == original_metadata


@pytest.mark.anyio
async def test_normal_snapshot_records_key_point_failure_as_explicit_partial_issue():
    original_metadata = deepcopy(FailedKeyPointSummaryRepository.metadata)
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    updated_context = await BuildPageSnapshotStep(
        cluster_repo_factory=SourceClusterRepository,
        summary_repo_factory=FailedKeyPointSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(EventRepository(session=RecordingAsyncSession(), events=[]), context)

    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert create_page['status'] == 'PARTIAL'
    assert create_page['partial_message'] == KEY_POINT_ISSUE['message']
    assert create_page['metadata_json'] == {
        'warnings': [],
        'issues': [KEY_POINT_ISSUE],
        'keyPoints': [],
    }
    assert updated_context.partial_message == KEY_POINT_ISSUE['message']
    assert FailedKeyPointSummaryRepository.metadata == original_metadata


@pytest.mark.anyio
async def test_normal_snapshot_normalizes_classifier_fallback_code_to_canonical_issue():
    class MissingThemesSourceClusterRepository(SourceClusterRepository):
        async def list_cluster_themes_by_business_date(self, business_date):
            _ = business_date
            return []

    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
        partial_reasons=[build_module.THEME_CLASSIFICATION_MISSING],
        partial_categories={build_module.THEME_CLASSIFICATION: 1},
    )

    await BuildPageSnapshotStep(
        cluster_repo_factory=MissingThemesSourceClusterRepository,
        summary_repo_factory=SuccessfulKeyPointSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(EventRepository(session=RecordingAsyncSession(), events=[]), context)

    page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert page['status'] == 'PARTIAL'
    assert page['partial_message'] == build_module.THEME_CLASSIFICATION_MISSING_MESSAGE
    assert page['metadata_json']['issues'] == [
        {
            'category': build_module.THEME_CLASSIFICATION,
            'code': build_module.THEME_CLASSIFICATION_MISSING,
            'message': build_module.THEME_CLASSIFICATION_MISSING_MESSAGE,
        }
    ]
    assert build_module.THEME_CLASSIFICATION_MISSING not in page['partial_message']
    assert context.partial_reasons == [
        build_module.THEME_CLASSIFICATION_MISSING_MESSAGE
    ]
    assert context.partial_categories == {build_module.THEME_CLASSIFICATION: 1}


@pytest.mark.anyio
@pytest.mark.parametrize(
    'invalid_field',
    ['processed_article_id', 'cluster_uid'],
    ids=['null-processed-article-id', 'null-cluster-uid'],
)
async def test_normal_snapshot_rejects_null_public_article_identity(invalid_field):
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())

    def cluster_repo_factory(session):
        repository = InvalidIdentityClusterRepository(session)
        repository.invalid_field = invalid_field
        return repository

    step = BuildPageSnapshotStep(
        cluster_repo_factory=cluster_repo_factory,
        summary_repo_factory=SuccessfulKeyPointSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    )
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    with pytest.raises(ValueError, match=invalid_field):
        await step.run(
            EventRepository(session=RecordingAsyncSession(), events=[]),
            context,
        )

    assert snapshot_repository.calls == []


@pytest.mark.anyio
async def test_normal_snapshot_redacts_provider_payload_from_page_and_event():
    class EmptySummaryRepository:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return []

    raw_provider_reason = (
        'AI summary fallback for GLOBAL_HEADLINE: 429 RESOURCE_EXHAUSTED '
        'quota RetryInfo secret-token https://generativelanguage.googleapis.com'
    )
    naver_reason = (
        'Naver news pagination cap was reached before covering the persisted '
        "window for keyword '증시'."
    )
    snapshot_repository = RecordingSnapshotRepository(RecordingAsyncSession())
    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
        fallback_count=1,
        partial_reasons=[naver_reason, raw_provider_reason],
    )

    await BuildPageSnapshotStep(
        cluster_repo_factory=SourceClusterRepository,
        summary_repo_factory=EmptySummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(repository, context)

    create_page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    serialized = f'{create_page!r} {repository.events!r}'
    assert naver_reason in create_page['partial_message']
    assert 'AI provider request failed; fallback content was used.' in serialized
    assert '429' not in serialized
    assert 'RetryInfo' not in serialized
    assert 'secret-token' not in serialized
    assert 'googleapis.com' not in serialized


class PersistedSearchDocumentPageRepository(ExistingPageRepository):
    async def get_page_markets(self, page_id):
        markets = await super().get_page_markets(page_id)
        markets[0]['search_document'] = '저장된 시장 문서  그대로'
        return markets

    async def get_page_clusters(self, page_market_ids):
        clusters = await super().get_page_clusters(page_market_ids)
        clusters[0]['search_document'] = '저장된 클러스터 문서  그대로'
        return clusters

    async def get_page_cluster_themes(self, page_market_cluster_ids):
        assert page_market_cluster_ids == [701]
        return [
            {
                'page_market_cluster_id': 701,
                'theme_code': 'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE',
                'rank': 1,
            },
            {
                'page_market_cluster_id': 701,
                'theme_code': 'MACRO_MONETARY_MARKETS_FX',
                'rank': 3,
            },
        ]


class ThemeRecordingSnapshotRepository(RecordingSnapshotRepository):
    def __init__(self, session):
        super().__init__(session)
        self.theme_rows: list[tuple[int, list[dict]]] = []

    async def insert_page_market_cluster_themes(self, page_cluster_id, themes):
        await super().insert_page_market_cluster_themes(page_cluster_id, themes)
        self.theme_rows.append((page_cluster_id, themes))


class NullClusterIdSnapshotRepository(ThemeRecordingSnapshotRepository):
    async def insert_page_market_cluster(self, params):
        self.calls.append(('insert_page_market_cluster', params))
        return None


@pytest.mark.anyio
async def test_normal_snapshot_rejects_missing_cluster_id_even_when_theme_rows_are_empty():
    snapshot_repository = NullClusterIdSnapshotRepository(RecordingAsyncSession())
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    with pytest.raises(RuntimeError, match='positive integer id'):
        await BuildPageSnapshotStep(
            cluster_repo_factory=ThemedSourceFactory(missing_themes=True),
            summary_repo_factory=MarketSummaryRepository,
            index_repo_factory=EmptyIndexRepository,
            snapshot_repo_factory=lambda session: snapshot_repository,
            context_repo_factory=CompleteMarketContextRepository,
        ).run(EventRepository(session=RecordingAsyncSession(), events=[]), context)

    assert snapshot_repository.theme_rows == []


@pytest.mark.anyio
async def test_rebuild_copies_persisted_search_documents_and_theme_ranks_verbatim():
    snapshot_repository = ThemeRecordingSnapshotRepository(RecordingAsyncSession())
    step = BuildPageSnapshotStep(
        cluster_repo_factory=FailingLiveRepository,
        summary_repo_factory=FailingLiveRepository,
        index_repo_factory=FailingLiveRepository,
        source_page_repo_factory=PersistedSearchDocumentPageRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
    )
    context = BatchExecutionContext(
        job_id=2002,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=True,
    )

    await step.run(
        EventRepository(session=RecordingAsyncSession(), events=[]),
        context,
    )

    market = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'create_page_market'
    )
    cluster = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'insert_page_market_cluster'
    )
    assert market['search_document'] == '저장된 시장 문서  그대로'
    assert cluster['search_document'] == '저장된 클러스터 문서  그대로'
    assert snapshot_repository.theme_rows == [
        (
            1002,
            [
                {
                    'theme_code': 'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE',
                    'rank': 1,
                },
                {'theme_code': 'MACRO_MONETARY_MARKETS_FX', 'rank': 3},
            ],
        )
    ]


class ThemedSourceClusterRepository:
    def __init__(self, session, *, missing_themes: bool = False):
        _ = session
        self.missing_themes = missing_themes

    async def list_clusters_by_business_date(self, business_date):
        _ = business_date
        return [
            {
                'id': 7001,
                'cluster_uid': 'cluster-uid-1',
                'market_type': 'US',
                'title': 'A ́ 클러스터',
                'summary_short': '첫 번째 요약',
                'tags_json': [],
                'representative_article_id': 4001,
                'representative_title': '첫 번째 대표 기사',
                'representative_publisher_name': '첫 매체',
                'representative_published_at': None,
                'representative_origin_link': 'https://example.com/1',
                'representative_naver_link': None,
                'article_count': 2,
            },
            {
                'id': 7002,
                'cluster_uid': 'cluster-uid-2',
                'market_type': 'US',
                'title': '두 번째 클러스터',
                'summary_short': '두 번째 요약',
                'tags_json': [],
                'representative_article_id': 4003,
                'representative_title': '두 번째 대표 기사',
                'representative_publisher_name': '둘째 매체',
                'representative_published_at': None,
                'representative_origin_link': 'https://example.com/3',
                'representative_naver_link': None,
                'article_count': 1,
            },
            {
                'id': 7003,
                'cluster_uid': 'cluster-uid-3',
                'market_type': 'KR',
                'title': '한국 시장 클러스터',
                'summary_short': '한국 시장 요약',
                'tags_json': [],
                'representative_article_id': None,
                'representative_title': None,
                'representative_publisher_name': None,
                'representative_published_at': None,
                'representative_origin_link': None,
                'representative_naver_link': None,
                'article_count': 0,
            },
        ]

    async def list_cluster_article_links_by_business_date(self, business_date):
        _ = business_date
        # Deliberately interleave clusters to prove the document groups by
        # source identity rather than by the input list position.
        return [
            {
                'market_type': 'US',
                'processed_article_id': 4002,
                'cluster_id': 7001,
                'cluster_uid': 'cluster-uid-1',
                'cluster_title': 'A ́ 클러스터',
                'title': '첫 번째 일반 기사',
                'publisher_name': '첫 매체',
                'published_at': None,
                'origin_link': 'https://example.com/2',
                'naver_link': None,
            },
            {
                'market_type': 'US',
                'processed_article_id': 4003,
                'cluster_id': 7002,
                'cluster_uid': 'cluster-uid-2',
                'cluster_title': '두 번째 클러스터',
                'title': '두 번째 일반 기사',
                'publisher_name': '둘째 매체',
                'published_at': None,
                'origin_link': 'https://example.com/3',
                'naver_link': None,
            },
            {
                'market_type': 'US',
                'processed_article_id': 4001,
                'cluster_id': 7001,
                'cluster_uid': 'cluster-uid-1',
                'cluster_title': 'A ́ 클러스터',
                'title': '첫 번째 대표 기사',
                'publisher_name': '첫 매체',
                'published_at': None,
                'origin_link': 'https://example.com/1',
                'naver_link': None,
            },
        ]

    async def list_cluster_themes_by_business_date(self, business_date):
        _ = business_date
        if self.missing_themes:
            return []
        return [
            {'cluster_id': 7001, 'theme_code': 'THEME_A', 'rank': 1},
            {'cluster_id': 7001, 'theme_code': 'THEME_B', 'rank': 2},
            {'cluster_id': 7001, 'theme_code': 'THEME_C', 'rank': 3},
            {'cluster_id': 7002, 'theme_code': 'THEME_Z', 'rank': 2},
            {'cluster_id': 7003, 'theme_code': 'THEME_KR', 'rank': 1},
        ]


class ThemedSourceFactory:
    def __init__(self, *, missing_themes: bool = False):
        self.missing_themes = missing_themes

    def __call__(self, session):
        return ThemedSourceClusterRepository(
            session, missing_themes=self.missing_themes
        )


class MarketSummaryRepository:
    def __init__(self, session):
        _ = session

    async def list_summaries_for_job(self, job_id):
        _ = job_id
        return [
            SimpleNamespace(
                summary_type='MARKET_SUMMARY',
                market_type='US',
                cluster_id=None,
                title='  US ́ 요약 ',
                body='본문\n내용',
                metadata_json={
                    'background': [' 배경 '],
                    'keyThemes': [' 테마 '],
                    'outlook': ' 전망 ',
                },
            )
        ]


@pytest.mark.anyio
async def test_normal_snapshot_groups_articles_and_preserves_one_to_three_theme_ranks():
    snapshot_repository = ThemeRecordingSnapshotRepository(RecordingAsyncSession())
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    await BuildPageSnapshotStep(
        cluster_repo_factory=ThemedSourceFactory(),
        summary_repo_factory=MarketSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(EventRepository(session=RecordingAsyncSession(), events=[]), context)

    market = next(
        payload
        for name, payload in snapshot_repository.calls
        if name == 'create_page_market'
    )
    assert market['search_document'] == (
        '미국 증시 일간 요약 us ́ 요약 본문 내용 배경 테마 전망'
    )
    clusters = [
        payload
        for name, payload in snapshot_repository.calls
        if name == 'insert_page_market_cluster'
    ]
    assert len(clusters) == 3
    first, second, third = clusters
    assert '첫 번째 일반 기사' in first['search_document']
    assert '두 번째 일반 기사' not in first['search_document']
    assert '두 번째 일반 기사' in second['search_document']
    assert third['title'] == '한국 시장 클러스터'
    assert snapshot_repository.theme_rows == [
        (
            1002,
            [
                {'theme_code': 'THEME_A', 'rank': 1},
                {'theme_code': 'THEME_B', 'rank': 2},
                {'theme_code': 'THEME_C', 'rank': 3},
            ],
        ),
        (1003, [{'theme_code': 'THEME_Z', 'rank': 2}]),
        (1004, [{'theme_code': 'THEME_KR', 'rank': 1}]),
    ]
    assert context.partial_reasons == []


@pytest.mark.anyio
async def test_normal_snapshot_missing_themes_is_partial_with_one_deduplicated_issue():
    snapshot_repository = ThemeRecordingSnapshotRepository(RecordingAsyncSession())
    repository = EventRepository(session=RecordingAsyncSession(), events=[])
    context = BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )

    await BuildPageSnapshotStep(
        cluster_repo_factory=ThemedSourceFactory(missing_themes=True),
        summary_repo_factory=MarketSummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda session: snapshot_repository,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(repository, context)

    page = next(
        payload for name, payload in snapshot_repository.calls if name == 'create_page'
    )
    assert page['status'] == 'PARTIAL'
    assert page['metadata_json']['issues'] == [
        {
            'category': 'THEME_CLASSIFICATION',
            'code': 'THEME_CLASSIFICATION_MISSING',
            'message': '일부 뉴스 주제의 검색 테마를 분류하지 못했습니다.',
        }
    ]
    assert [
        event['context_json']['clusterId']
        for event in repository.events
        if event.get('context_json', {}).get('reason') == 'THEME_CLASSIFICATION_MISSING'
    ] == [7001, 7002, 7003]
