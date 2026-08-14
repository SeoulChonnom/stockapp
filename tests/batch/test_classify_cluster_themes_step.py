from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import THEME_CLASSIFIER_PROMPT_VERSION
from app.batch.steps.build_page_snapshot import BuildPageSnapshotStep
from app.batch.steps.classify_cluster_themes import (
    CLASSIFY_CLUSTER_THEMES,
    THEME_CLASSIFICATION,
    THEME_CLASSIFICATION_MISSING,
    ClassifyClusterThemesStep,
)
from app.batch.theme_rules import CANONICAL_LEAF_CODES
from app.core.llm import LlmRetryableError
from app.db.repositories.projections import ThemeAssignmentCreateParams
from tests.batch.theme_test_support import active_theme_tree_rows
from tests.market_context_fakes import CompleteMarketContextRepository
from tests.support import RecordingAsyncSession

BUSINESS_DATE = date(2026, 3, 17)
THEME_CODE = 'SECTOR_SEMICONDUCTORS_MEMORY_HBM'


@dataclass
class FakeBatchRepository:
    session: RecordingAsyncSession
    checkpoint: dict
    events: list[dict]
    lease_token: UUID | None = None

    async def get_job_by_id(self, _job_id):
        return SimpleNamespace(
            checkpoint_json=self.checkpoint,
            lease_token=self.lease_token,
        )

    async def save_checkpoint(self, **kwargs):
        self.checkpoint = kwargs['checkpoint_json']
        return True

    async def commit(self):
        await self.session.commit()

    async def add_event(self, **kwargs):
        self.events.append(kwargs)


class FakeThemeRepository:
    def __init__(self, _session):
        self.rows = active_theme_tree_rows()

    async def list_active_tree_rows(self):
        return self.rows


class FakeClusterRepository:
    def __init__(self, _session, *, clusters=None, articles=None, processed=None):
        self.clusters = clusters or [
            {
                'id': 7001,
                'market_type': 'US',
                'cluster_rank': 1,
                'title': '반도체 수요 증가',
                'representative_article_id': 42,
            }
        ]
        self.articles = articles or {
            7001: [
                {'processed_article_id': 42, 'article_rank': 1},
            ]
        }
        self.processed = processed or {
            42: {
                'id': 42,
                'canonical_title': 'HBM 수요가 늘었다',
                'source_summary': '데이터센터 투자가 확대됐다.',
                'article_body_excerpt': '메모리 업황 개선 기대',
            }
        }
        self.replacements: list[tuple[int, list]] = []
        self.list_calls = 0

    async def list_clusters_by_business_date(self, _business_date):
        self.list_calls += 1
        return self.clusters

    async def get_cluster_articles(self, cluster_id):
        return self.articles[cluster_id]

    async def get_processed_articles(self, article_ids):
        return [self.processed[article_id] for article_id in article_ids]

    async def replace_cluster_themes(self, cluster_id, assignments):
        self.replacements.append((cluster_id, list(assignments)))


class FakeProvider:
    concurrency_limit = 1

    def __init__(self, result=None, *, configured=True, error=None):
        self.result = result if result is not None else {'themeCodes': [THEME_CODE]}
        self.configured = configured
        self.error = error
        self.calls: list[dict] = []

    def is_configured(self):
        return self.configured

    async def classify_cluster_themes(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def _context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )


def _step(provider, cluster_repository):
    return ClassifyClusterThemesStep(
        cluster_repo_factory=lambda _session: cluster_repository,
        theme_repository_factory=FakeThemeRepository,
        llm_provider_factory=lambda: provider,
    )


@pytest.mark.anyio
async def test_classification_step_persists_llm_ranks_and_versioned_target():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(
        session,
        {},
        [],
        lease_token=UUID('00000000-0000-0000-0000-000000000123'),
    )
    cluster_repository = FakeClusterRepository(session)
    provider = FakeProvider()

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assert cluster_repository.replacements == [
        (
            7001,
            [
                ThemeAssignmentCreateParams(
                    theme_code=THEME_CODE,
                    rank=1,
                    classification_method='LLM',
                )
            ],
        )
    ]
    target_key = repository.checkpoint['targetProgress'][CLASSIFY_CLUSTER_THEMES][0]
    assert '7001' in target_key
    assert THEME_CLASSIFIER_PROMPT_VERSION in target_key
    assert updated.partial_reasons == []


@pytest.mark.anyio
async def test_zero_valid_output_uses_fallback_and_miss_clears_themes_once():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)
    provider = FakeProvider(result={'themeCodes': ['PARENT', 'UNKNOWN']})

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assert cluster_repository.replacements == [(7001, [])]
    assert updated.partial_reasons == [THEME_CLASSIFICATION_MISSING]
    assert updated.partial_categories == {THEME_CLASSIFICATION: 1}
    assert (
        sum(
            event.get('context_json', {}).get('reason') == THEME_CLASSIFICATION_MISSING
            for event in repository.events
        )
        == 1
    )


@pytest.mark.anyio
async def test_classifier_missing_fallback_becomes_one_canonical_snapshot_issue():
    class SnapshotClusterRepository(FakeClusterRepository):
        def __init__(self, session):
            super().__init__(session)
            self.clusters[0].update(
                {
                    'cluster_uid': 'cluster-uid',
                    'summary_short': '클러스터 요약',
                    'article_count': 1,
                    'representative_title': '대표 기사',
                    'representative_publisher_name': '매체',
                    'representative_published_at': None,
                    'representative_origin_link': 'https://example.com/article',
                    'representative_naver_link': None,
                    'article_grouping_status': 'UNAVAILABLE',
                    'article_grouping_generated_at': None,
                    'article_grouping_issue_code': 'SIMILARITY_GROUPING_FAILED',
                    'article_grouping_algorithm_version': 'v1',
                    'article_grouping_algorithm_version_count': 1,
                }
            )

        async def list_cluster_article_links_by_business_date(self, _business_date):
            return [
                {
                    'market_type': 'US',
                    'processed_article_id': 42,
                    'cluster_id': 7001,
                    'cluster_uid': 'cluster-uid',
                    'cluster_title': '반도체 수요 증가',
                    'title': 'HBM 수요가 늘었다',
                    'publisher_name': '매체',
                    'published_at': None,
                    'origin_link': 'https://example.com/article',
                    'article_grouping_status': 'UNAVAILABLE',
                    'article_grouping_generated_at': None,
                    'article_grouping_issue_code': 'SIMILARITY_GROUPING_FAILED',
                    'article_grouping_algorithm_version': 'v1',
                    'similar_group_rank': 1,
                    'is_similar_group_representative': True,
                    'exact_duplicate_count': 0,
                    'naver_link': None,
                }
            ]

        async def list_cluster_themes_by_business_date(self, _business_date):
            return []

    class EmptySummaryRepository:
        def __init__(self, _session):
            pass

        async def list_summaries_for_job(self, _job_id):
            return []

    class EmptyIndexRepository:
        def __init__(self, _session):
            pass

        async def list_indices_by_business_date(self, _business_date):
            return []

    class SnapshotWriter:
        def __init__(self):
            self.page = None
            self.theme_rows = []
            self.next_cluster_id = 9001

        async def get_next_version_no(self, _business_date):
            return 1

        async def create_page(self, **kwargs):
            self.page = kwargs
            return 501

        async def create_page_market(self, **_kwargs):
            return 601

        async def insert_page_market_index(self, _params):
            return None

        async def insert_page_market_cluster(self, _params):
            cluster_id = self.next_cluster_id
            self.next_cluster_id += 1
            return cluster_id

        async def insert_page_market_cluster_themes(self, cluster_id, themes):
            self.theme_rows.append((cluster_id, themes))

        async def insert_page_article_link(self, _params):
            return None

    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    context = _context()
    cluster_repository = SnapshotClusterRepository(session)
    await _step(
        FakeProvider(result={'themeCodes': ['PARENT', 'UNKNOWN']}),
        cluster_repository,
    ).run(repository, context)

    snapshot_writer = SnapshotWriter()
    await BuildPageSnapshotStep(
        cluster_repo_factory=lambda _session: cluster_repository,
        summary_repo_factory=EmptySummaryRepository,
        index_repo_factory=EmptyIndexRepository,
        snapshot_repo_factory=lambda _session: snapshot_writer,
        context_repo_factory=CompleteMarketContextRepository,
    ).run(repository, context)

    assert snapshot_writer.page['status'] == 'PARTIAL'
    assert snapshot_writer.page['partial_message'] == (
        '일부 뉴스 주제의 검색 테마를 분류하지 못했습니다.'
    )
    assert snapshot_writer.page['metadata_json']['issues'] == [
        {
            'category': 'THEME_CLASSIFICATION',
            'code': 'THEME_CLASSIFICATION_MISSING',
            'message': '일부 뉴스 주제의 검색 테마를 분류하지 못했습니다.',
        }
    ]
    assert context.partial_reasons == [
        '일부 뉴스 주제의 검색 테마를 분류하지 못했습니다.'
    ]
    assert context.partial_categories == {'THEME_CLASSIFICATION': 1}
    assert snapshot_writer.theme_rows == [(9001, [])]


@pytest.mark.anyio
async def test_mixed_subset_keeps_valid_order_and_persists_contiguous_llm_ranks():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)
    provider = FakeProvider(
        result={
            'themeCodes': [
                'UNKNOWN',
                CANONICAL_LEAF_CODES[7],
                CANONICAL_LEAF_CODES[7],
                CANONICAL_LEAF_CODES[8],
                CANONICAL_LEAF_CODES[9],
                CANONICAL_LEAF_CODES[10],
            ]
        }
    )

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assignments = cluster_repository.replacements[0][1]
    assert [assignment.theme_code for assignment in assignments] == list(
        CANONICAL_LEAF_CODES[7:10]
    )
    assert [assignment.rank for assignment in assignments] == [1, 2, 3]
    assert all(assignment.classification_method == 'LLM' for assignment in assignments)
    assert updated.fallback_count == 0
    assert updated.partial_reasons == []


@pytest.mark.anyio
async def test_unconfigured_provider_uses_shared_fallback_without_calling_provider():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)
    cluster_repository.clusters[0]['title'] = 'HBM3E 메모리 반도체 수요'
    cluster_repository.processed[42]['canonical_title'] = 'HBM3E 메모리 반도체'
    cluster_repository.processed[42]['source_summary'] = 'HBM 메모리 대역폭 확대'
    provider = FakeProvider(configured=False)

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assert provider.calls == []
    assignments = cluster_repository.replacements[0][1]
    assert assignments
    assert all(
        assignment.classification_method == 'KEYWORD_FALLBACK'
        for assignment in assignments
    )
    assert updated.fallback_count == 1
    assert updated.partial_reasons == []


@pytest.mark.anyio
async def test_non_retryable_provider_error_uses_shared_fallback():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)
    cluster_repository.clusters[0]['title'] = 'HBM3E 메모리 반도체 수요'
    cluster_repository.processed[42]['canonical_title'] = 'HBM3E 메모리 반도체'
    cluster_repository.processed[42]['source_summary'] = 'HBM 메모리 대역폭 확대'
    provider = FakeProvider(error=RuntimeError('provider unavailable'))

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assert cluster_repository.replacements[0][1]
    assert updated.fallback_count == 1
    assert updated.partial_reasons == []
    assert repository.events[0]['context_json']['fallbackReason'] == 'provider_error'


@pytest.mark.anyio
async def test_catalog_mismatch_is_fatal_before_persisted_cluster_read():
    class MismatchThemeRepository(FakeThemeRepository):
        async def list_active_tree_rows(self):
            return [
                SimpleNamespace(
                    code='UNKNOWN_ACTIVE_LEAF',
                    parent_code=None,
                    is_active=True,
                )
            ]

    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)

    with pytest.raises(ValueError):
        await ClassifyClusterThemesStep(
            cluster_repo_factory=lambda _session: cluster_repository,
            theme_repository_factory=MismatchThemeRepository,
            llm_provider_factory=lambda: FakeProvider(),
        ).run(repository, _context())

    assert cluster_repository.list_calls == 0
    assert cluster_repository.replacements == []


@pytest.mark.anyio
async def test_theme_repository_read_error_is_fatal_before_cluster_read():
    class FailingThemeRepository(FakeThemeRepository):
        async def list_active_tree_rows(self):
            raise RuntimeError('theme read failed')

    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)

    with pytest.raises(RuntimeError, match='theme read failed'):
        await ClassifyClusterThemesStep(
            cluster_repo_factory=lambda _session: cluster_repository,
            theme_repository_factory=FailingThemeRepository,
            llm_provider_factory=lambda: FakeProvider(),
        ).run(repository, _context())

    assert cluster_repository.list_calls == 0


@pytest.mark.anyio
async def test_theme_write_error_is_fatal_before_checkpoint_or_commit():
    class TrackingRepository(FakeBatchRepository):
        def __init__(self, session):
            super().__init__(
                session,
                {},
                [],
                lease_token=UUID('00000000-0000-0000-0000-000000000123'),
            )
            self.save_calls = 0
            self.commit_calls = 0

        async def save_checkpoint(self, **kwargs):
            self.save_calls += 1
            return await super().save_checkpoint(**kwargs)

        async def commit(self):
            self.commit_calls += 1
            await super().commit()

    class FailingWriter:
        async def replace_cluster_themes(self, _cluster_id, _assignments):
            raise RuntimeError('theme write failed')

    session = RecordingAsyncSession()
    repository = TrackingRepository(session)
    cluster_repository = FakeClusterRepository(session)

    with pytest.raises(RuntimeError, match='theme write failed'):
        await ClassifyClusterThemesStep(
            cluster_repo_factory=lambda _session: cluster_repository,
            cluster_write_repo_factory=lambda _session: FailingWriter(),
            theme_repository_factory=FakeThemeRepository,
            llm_provider_factory=lambda: FakeProvider(),
        ).run(repository, _context())

    assert repository.save_calls == 0
    assert repository.commit_calls == 0


@pytest.mark.anyio
async def test_classification_uses_one_session_and_replaces_before_checkpoint_commit():
    operations: list[str] = []
    session = RecordingAsyncSession()

    class OrderedRepository(FakeBatchRepository):
        async def add_event(self, **kwargs):
            operations.append('event')
            await super().add_event(**kwargs)

        async def save_checkpoint(self, **kwargs):
            operations.append('checkpoint')
            return await super().save_checkpoint(**kwargs)

        async def commit(self):
            operations.append('commit')
            await super().commit()

    class OrderedWriter:
        def __init__(self, factory_session):
            self.factory_session = factory_session

        async def replace_cluster_themes(self, _cluster_id, _assignments):
            operations.append('replace_themes')

    repository = OrderedRepository(
        session,
        {},
        [],
        lease_token=UUID('00000000-0000-0000-0000-000000000123'),
    )
    factory_sessions: dict[str, object] = {}
    cluster_repository = FakeClusterRepository(session)

    def cluster_factory(factory_session):
        factory_sessions['cluster'] = factory_session
        return cluster_repository

    def writer_factory(factory_session):
        factory_sessions['writer'] = factory_session
        return OrderedWriter(factory_session)

    def theme_factory(factory_session):
        factory_sessions['theme'] = factory_session
        return FakeThemeRepository(factory_session)

    await ClassifyClusterThemesStep(
        cluster_repo_factory=cluster_factory,
        cluster_write_repo_factory=writer_factory,
        theme_repository_factory=theme_factory,
        llm_provider_factory=lambda: FakeProvider(error=RuntimeError('fallback')),
    ).run(repository, _context())

    assert factory_sessions == {
        'cluster': session,
        'writer': session,
        'theme': session,
    }
    assert operations.index('replace_themes') < operations.index('event')
    assert operations.index('event') < operations.index('checkpoint')
    assert operations.index('checkpoint') < operations.index('commit')


@pytest.mark.anyio
async def test_resume_skips_only_completed_versioned_targets():
    session = RecordingAsyncSession()
    cluster_rows = [
        {
            'id': 7001,
            'market_type': 'US',
            'cluster_rank': 1,
            'title': '첫 클러스터',
            'representative_article_id': 42,
        },
        {
            'id': 7002,
            'market_type': 'US',
            'cluster_rank': 2,
            'title': '둘째 클러스터',
            'representative_article_id': 43,
        },
    ]
    cluster_repository = FakeClusterRepository(
        session,
        clusters=cluster_rows,
        articles={
            7001: [{'processed_article_id': 42, 'article_rank': 1}],
            7002: [{'processed_article_id': 43, 'article_rank': 1}],
        },
        processed={
            42: {
                'id': 42,
                'canonical_title': '첫 기사',
                'source_summary': '첫 요약',
                'article_body_excerpt': '첫 본문',
            },
            43: {
                'id': 43,
                'canonical_title': '둘째 기사',
                'source_summary': '둘째 요약',
                'article_body_excerpt': '둘째 본문',
            },
        },
    )
    first_key = f'cluster:7001:theme-classifier:{THEME_CLASSIFIER_PROMPT_VERSION}'
    repository = FakeBatchRepository(
        session,
        {'targetProgress': {CLASSIFY_CLUSTER_THEMES: [first_key]}},
        [],
    )
    provider = FakeProvider(result={'themeCodes': [CANONICAL_LEAF_CODES[7]]})

    await _step(provider, cluster_repository).run(repository, _context())

    assert [cluster_id for cluster_id, _ in cluster_repository.replacements] == [7002]
    assert [call['cluster']['title'] for call in provider.calls] == ['둘째 클러스터']
    assert repository.checkpoint == {
        'targetProgress': {CLASSIFY_CLUSTER_THEMES: [first_key]}
    }


@pytest.mark.anyio
async def test_multiple_missing_targets_keep_one_deduplicated_partial_reason():
    session = RecordingAsyncSession()
    clusters = [
        {
            'id': 7001,
            'market_type': 'US',
            'cluster_rank': 1,
            'title': '첫 클러스터',
            'representative_article_id': 42,
        },
        {
            'id': 7002,
            'market_type': 'US',
            'cluster_rank': 2,
            'title': '둘째 클러스터',
            'representative_article_id': 43,
        },
    ]
    cluster_repository = FakeClusterRepository(
        session,
        clusters=clusters,
        articles={
            7001: [{'processed_article_id': 42, 'article_rank': 1}],
            7002: [{'processed_article_id': 43, 'article_rank': 1}],
        },
        processed={
            42: {
                'id': 42,
                'canonical_title': '날씨 뉴스',
                'source_summary': '새로운 소식',
                'article_body_excerpt': '관련 내용',
            },
            43: {
                'id': 43,
                'canonical_title': '스포츠 뉴스',
                'source_summary': '경기 결과',
                'article_body_excerpt': '선수 인터뷰',
            },
        },
    )
    repository = FakeBatchRepository(session, {}, [])
    provider = FakeProvider(result={'themeCodes': ['UNKNOWN']})

    updated = await _step(provider, cluster_repository).run(repository, _context())

    assert cluster_repository.replacements == [(7001, []), (7002, [])]
    assert updated.partial_reasons == [THEME_CLASSIFICATION_MISSING]
    assert updated.partial_categories == {THEME_CLASSIFICATION: 1}
    assert (
        sum(
            event.get('context_json', {}).get('reason') == THEME_CLASSIFICATION_MISSING
            for event in repository.events
        )
        == 2
    )


@pytest.mark.anyio
async def test_retryable_provider_error_does_not_write_or_checkpoint():
    session = RecordingAsyncSession()
    repository = FakeBatchRepository(session, {}, [])
    cluster_repository = FakeClusterRepository(session)
    provider = FakeProvider(error=LlmRetryableError())

    with pytest.raises(LlmRetryableError):
        await _step(provider, cluster_repository).run(repository, _context())

    assert cluster_repository.replacements == []
    assert repository.checkpoint == {}
