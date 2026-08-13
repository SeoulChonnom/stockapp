from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.batch.ai_retry.models import AiRetryPageResult, AiRetrySelection
from app.batch.ai_retry.orchestrator import (
    AI_RETRY_SELECT_STEP,
    AiRetryOrchestrator,
    _generate_target,
    _retry_create_params,
)
from app.batch.ai_retry.page_builder import _build_page_issues
from app.batch.ai_summary_targets import AiSummaryTarget
from app.batch.exceptions import BatchLeaseLostError
from app.batch.providers.llm_provider import BatchLlmProvider
from app.db.repositories.ai_retry_repo import AiRetryJob
from app.db.repositories.projections import AiSummaryRecord
from tests.batch.gemini_mock import build_mock_gemini_harness, gemini_ai_message

BUSINESS_DATE = date(2026, 7, 28)
GENERATED_AT = datetime(2026, 7, 29, tzinfo=UTC)
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


class FakeSessionContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakeSessionMaker:
    def __call__(self):
        return FakeSessionContext()


class FakeRetryRepository:
    def __init__(self, job):
        self.job = job
        self.completed = None
        self.commits = 0
        self.rollbacks = 0

    async def get_job(self, job_id):
        assert job_id == self.job.job_id
        return self.job

    async def complete_job(self, **kwargs):
        self.completed = kwargs
        return True

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeJobRepository:
    def __init__(self):
        self.events = []
        self.begun_steps: list[str] = []
        self.step_run_seq = 0
        self.finished_step_runs: list[dict] = []

    async def add_event(self, **kwargs):
        self.events.append(kwargs)

    async def begin_step(self, *, job_id, lease_token, step_code):
        self.begun_steps.append(step_code)
        self.step_run_seq += 1
        return self.step_run_seq

    async def finish_step_run(
        self,
        *,
        step_run_id,
        status,
        error_message=None,
        error_log=None,
    ):
        self.finished_step_runs.append(
            {
                'step_run_id': step_run_id,
                'status': status,
                'error_message': error_message,
                'error_log': error_log,
            }
        )
        return True


class OperationLoggingSession:
    """Reproduces the real AsyncSession semantics that matter for this bug:

    an INSERT/UPDATE issued through `begin_step`/`finish_step_run` is only
    durable once `commit()` runs; `rollback()` discards anything issued
    since the last commit. `operations` records the order calls happen in,
    across every fake wired to this shared session, so tests can assert on
    interleaving (e.g. "commit happens before the step's own work runs").
    """

    def __init__(self, *, fail_commit_on: set[int] | None = None):
        self.pending_steps: dict[int, str] = {}
        self.committed_steps: dict[int, str] = {}
        self.pending_step_runs: dict[int, dict] = {}
        self.committed_step_runs: dict[int, dict] = {}
        self.next_id = 0
        self.commit_attempts = 0
        self.fail_commit_on = set(fail_commit_on or ())
        self.operations: list[tuple] = []
        self.finished_step_runs: list[dict] = []

    def begin_step(self, step_code):
        self.next_id += 1
        step_run_id = self.next_id
        self.pending_steps[step_run_id] = 'RUNNING'
        self.pending_step_runs[step_run_id] = {
            'status': 'RUNNING',
            'error_message': None,
            'error_log': None,
        }
        self.operations.append(('begin_step', step_code, step_run_id))
        return step_run_id

    def finish_step_run(
        self,
        step_run_id,
        status,
        error_message=None,
        error_log=None,
    ):
        current = self.pending_steps.get(
            step_run_id, self.committed_steps.get(step_run_id)
        )
        if current != 'RUNNING':
            self.operations.append(('finish_step_run_noop', step_run_id, status))
            return False
        self.pending_steps[step_run_id] = status
        self.pending_step_runs[step_run_id] = {
            'status': status,
            'error_message': error_message,
            'error_log': error_log,
        }
        self.operations.append(('finish_step_run', step_run_id, status))
        self.finished_step_runs.append(
            {
                'step_run_id': step_run_id,
                'status': status,
                'error_message': error_message,
                'error_log': error_log,
            }
        )
        return True

    def commit(self):
        self.commit_attempts += 1
        if self.commit_attempts in self.fail_commit_on:
            self.fail_commit_on.remove(self.commit_attempts)
            self.operations.append(('commit_failed', self.commit_attempts))
            raise RuntimeError('commit failed token=secret-token')
        self.committed_steps.update(self.pending_steps)
        self.committed_step_runs.update(self.pending_step_runs)
        self.pending_steps = {}
        self.pending_step_runs = {}
        self.operations.append(('commit',))

    def rollback(self):
        self.pending_steps = {}
        self.pending_step_runs = {}
        self.operations.append(('rollback',))

    def log(self, marker):
        self.operations.append(marker)


class SessionJobRepository:
    def __init__(self, session):
        self.session = session
        self.events = []

    async def add_event(self, **kwargs):
        self.events.append(kwargs)

    async def begin_step(self, *, job_id, lease_token, step_code):
        return self.session.begin_step(step_code)

    async def finish_step_run(
        self,
        *,
        step_run_id,
        status,
        error_message=None,
        error_log=None,
    ):
        return self.session.finish_step_run(
            step_run_id,
            status,
            error_message,
            error_log,
        )


class CheckpointSessionJobRepository(SessionJobRepository):
    def __init__(self, session, checkpoint_results):
        super().__init__(session)
        self.checkpoint_results = iter(checkpoint_results)

    async def save_checkpoint(self, **kwargs):
        self.session.log(('save_checkpoint', kwargs['current_step']))
        return next(self.checkpoint_results)


class SessionRetryRepository:
    def __init__(self, session, job):
        self.session = session
        self.job = job
        self.completed = None

    async def get_job(self, job_id):
        assert job_id == self.job.job_id
        return self.job

    async def complete_job(self, **kwargs):
        self.completed = kwargs
        return True

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


class RaisingSummaryRepository:
    """Fails mid-SELECT-step, after `begin_step` but before any checkpoint."""

    def __init__(self, session):
        self.session = session

    async def list_retry_lineage_summaries(self, source_job_id):
        self.session.log(('list_retry_lineage_summaries', source_job_id))
        raise RuntimeError('lineage lookup failed token=secret-token')


class FakeSummaryRepository:
    def __init__(self, lineage):
        self.lineage = lineage

    async def list_retry_lineage_summaries(self, source_job_id):
        assert source_job_id == 10
        return list(self.lineage)


class FakeSummaryWriteRepository:
    def __init__(self):
        self.params = []

    async def upsert_retry_summary(self, params):
        self.params.append(params)
        return AiSummaryRecord(
            summary_id=100 + len(self.params),
            batch_job_id=params.batch_job_id,
            summary_type=params.summary_type,
            business_date=params.business_date,
            market_type=params.market_type,
            cluster_id=params.cluster_id,
            title=params.title,
            body=params.body,
            paragraphs_json=params.paragraphs_json,
            model_name=params.model_name,
            prompt_version=params.prompt_version,
            status=params.status,
            fallback_used=params.fallback_used,
            error_message=params.error_message,
            metadata_json=params.metadata_json,
            generated_at=GENERATED_AT,
            target_key=params.target_key,
            source_summary_id=params.source_summary_id,
            attempt_no=params.attempt_no,
        )


class FakeClusterRepository:
    async def list_clusters_by_business_date(self, business_date):
        assert business_date == BUSINESS_DATE
        return []


class FakeIndexRepository:
    async def list_indices_by_business_date(self, business_date):
        assert business_date == BUSINESS_DATE
        return []


class SuccessfulLlm:
    model_name = 'gemini'

    def is_configured(self):
        return True

    async def summarize_global_headline(self, **_kwargs):
        return {'title': 'recovered', 'body': 'recovered body'}

    async def summarize_key_points(self, **_kwargs):
        return {'keyPoints': KEY_POINTS}


class KeyPointFailureLlm(SuccessfulLlm):
    async def summarize_key_points(self, **_kwargs):
        return {'keyPoints': [{'kind': 'direction'}]}


class KeyPointOnlyLlm(SuccessfulLlm):
    def __init__(self, *, fail: bool = False):
        self.headline_calls = 0
        self.key_point_calls = 0
        self.fail = fail

    async def summarize_global_headline(self, **_kwargs):
        self.headline_calls += 1
        raise AssertionError('headline must not be regenerated')

    async def summarize_key_points(self, **_kwargs):
        self.key_point_calls += 1
        return (
            {'keyPoints': [{'kind': 'direction'}]}
            if self.fail
            else {'keyPoints': KEY_POINTS}
        )


class FullGlobalTrackingLlm(SuccessfulLlm):
    def __init__(self):
        self.headline_calls = 0
        self.key_point_calls = 0

    async def summarize_global_headline(self, **_kwargs):
        self.headline_calls += 1
        return {'title': 'regenerated headline', 'body': 'regenerated body'}

    async def summarize_key_points(self, **_kwargs):
        self.key_point_calls += 1
        return {'keyPoints': KEY_POINTS}


class TimeoutLlm(SuccessfulLlm):
    async def summarize_global_headline(self, **_kwargs):
        raise TimeoutError('provider timeout')


class FakePageBuilder:
    def __init__(self, *, status='READY'):
        self.status = status
        self.calls = []

    async def build(self, **kwargs):
        self.calls.append(kwargs)
        return AiRetryPageResult(
            page_id=777,
            version_no=4,
            status=self.status,
            partial_message=None,
        )


def _source_summary() -> AiSummaryRecord:
    return AiSummaryRecord(
        summary_id=1,
        batch_job_id=10,
        summary_type='GLOBAL_HEADLINE',
        business_date=BUSINESS_DATE,
        market_type=None,
        cluster_id=None,
        title='fallback',
        body=None,
        paragraphs_json=[],
        model_name=None,
        prompt_version='v1',
        status='FALLBACK',
        fallback_used=True,
        error_message='429',
        metadata_json={},
        generated_at=GENERATED_AT,
        target_key='GLOBAL_HEADLINE',
    )


def _retry_job(*, checkpoint_json=None) -> AiRetryJob:
    return AiRetryJob(
        job_id=20,
        job_name='market_daily_batch',
        business_date=BUSINESS_DATE,
        status='RUNNING',
        run_mode='AI_RETRY',
        source_job_id=10,
        source_page_id=501,
        idempotency_key='retry-20',
        started_at=GENERATED_AT,
        checkpoint_json=checkpoint_json,
    )


def _orchestrator(*, lineage, llm, page_builder, job=None):
    retry_repo = FakeRetryRepository(job or _retry_job())
    job_repo = FakeJobRepository()
    summary_writer = FakeSummaryWriteRepository()
    orchestrator = AiRetryOrchestrator(
        session_maker=FakeSessionMaker(),
        retry_repo_factory=lambda _: retry_repo,
        job_repo_factory=lambda _: job_repo,
        summary_repo_factory=lambda _: FakeSummaryRepository(lineage),
        summary_write_repo_factory=lambda _: summary_writer,
        cluster_repo_factory=lambda _: FakeClusterRepository(),
        index_repo_factory=lambda _: FakeIndexRepository(),
        llm_provider_factory=lambda: llm,
        page_builder=page_builder,
    )
    return orchestrator, retry_repo, summary_writer, job_repo


def _build_successful_retry():
    job = replace(_retry_job(), job_id=4001)
    page_builder = FakePageBuilder()
    orchestrator, _retry_repo, _summary_writer, job_repo = _orchestrator(
        lineage=[_source_summary()],
        llm=SuccessfulLlm(),
        page_builder=page_builder,
        job=job,
    )
    lease_token = uuid4()
    return job_repo, orchestrator, lease_token


def _build_failing_retry():
    """A retry job whose SELECT step begins, then blows up before checkpointing.

    Uses `OperationLoggingSession`-backed fakes (not `FakeRetryRepository`,
    whose `rollback()` is just a counter) so that `rollback()` actually
    discards any step_run row that was never committed -- the real
    AsyncSession semantics that Finding 1 depends on.
    """
    session = OperationLoggingSession()
    job = replace(_retry_job(), job_id=4002)
    job_repo = SessionJobRepository(session)
    retry_repo = SessionRetryRepository(session, job)
    orchestrator = AiRetryOrchestrator(
        session_maker=FakeSessionMaker(),
        retry_repo_factory=lambda _: retry_repo,
        job_repo_factory=lambda _: job_repo,
        summary_repo_factory=lambda _: RaisingSummaryRepository(session),
        summary_write_repo_factory=lambda _: FakeSummaryWriteRepository(),
        cluster_repo_factory=lambda _: FakeClusterRepository(),
        index_repo_factory=lambda _: FakeIndexRepository(),
        llm_provider_factory=lambda: SuccessfulLlm(),
        page_builder=FakePageBuilder(),
    )
    lease_token = uuid4()
    return session, orchestrator, lease_token


def _build_transactional_successful_retry(
    *,
    checkpoint_results: list[bool],
    fail_commit_on: set[int] | None = None,
):
    session = OperationLoggingSession(fail_commit_on=fail_commit_on)
    job = replace(_retry_job(), job_id=4003)
    job_repo = CheckpointSessionJobRepository(session, checkpoint_results)
    retry_repo = SessionRetryRepository(session, job)
    orchestrator = AiRetryOrchestrator(
        session_maker=FakeSessionMaker(),
        retry_repo_factory=lambda _: retry_repo,
        job_repo_factory=lambda _: job_repo,
        summary_repo_factory=lambda _: FakeSummaryRepository([_source_summary()]),
        summary_write_repo_factory=lambda _: FakeSummaryWriteRepository(),
        cluster_repo_factory=lambda _: FakeClusterRepository(),
        index_repo_factory=lambda _: FakeIndexRepository(),
        llm_provider_factory=lambda: SuccessfulLlm(),
        page_builder=FakePageBuilder(),
    )
    return session, orchestrator, uuid4()


@pytest.mark.anyio
async def test_recovered_target_creates_vnext_and_completes_success():
    source = _source_summary()
    page_builder = FakePageBuilder()
    orchestrator, retry_repo, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=SuccessfulLlm(),
        page_builder=page_builder,
    )

    result = await orchestrator.run(20)

    assert result.status == 'SUCCESS'
    assert result.counts.recovered_count == 1
    assert result.page is not None
    assert len(page_builder.calls) == 1
    assert summary_writer.params[0].source_summary_id == source.summary_id
    assert source.status == 'FALLBACK'
    assert retry_repo.completed['page_id'] == 777


@pytest.mark.anyio
async def test_ai_retry_uses_retry_upsert_boundary():
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[_source_summary()],
        llm=SuccessfulLlm(),
        page_builder=FakePageBuilder(),
    )

    await orchestrator.run(20)

    assert len(summary_writer.params) == 1


@pytest.mark.anyio
async def test_fallback_global_headline_retries_headline_and_keypoints_together():
    source = replace(
        _source_summary(),
        title='fallback headline',
        status='FALLBACK',
        fallback_used=True,
        metadata_json={
            'keyPointIssue': {
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
            }
        },
    )
    selection = AiRetrySelection(
        target=AiSummaryTarget(
            target_key='GLOBAL_HEADLINE',
            summary_type='GLOBAL_HEADLINE',
            market_type=None,
            cluster_id=None,
        ),
        source_summary=source,
    )
    provider = FullGlobalTrackingLlm()

    payload = await _generate_target(
        selection,
        llm_provider=provider,
        cluster_repo=FakeClusterRepository(),
        clusters=[],
        indices=[],
    )

    assert payload['title'] == 'regenerated headline'
    assert payload['body'] == 'regenerated body'
    assert provider.headline_calls == 1
    assert provider.key_point_calls == 1


@pytest.mark.anyio
async def test_recovered_global_target_persists_v2_outputs_and_retry_metadata():
    source = _source_summary()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=SuccessfulLlm(),
        page_builder=FakePageBuilder(),
    )

    result = await orchestrator.run(20)

    persisted = summary_writer.params[0]
    assert result.counts.success_count == 1
    assert result.counts.recovered_count == 1
    assert persisted.prompt_version == 'v2'
    assert persisted.status == 'SUCCESS'
    assert persisted.fallback_used is False
    assert persisted.metadata_json == {
        'reason': 'llm',
        'keyPoints': KEY_POINTS,
        'keyPointIssue': None,
        'retry': {'sourceSummaryId': source.summary_id, 'attemptNo': 2},
    }


@pytest.mark.anyio
async def test_cluster_detail_retry_persists_v2_grounded_shape_without_legacy(
    monkeypatch,
):
    source = replace(
        _source_summary(),
        summary_type='CLUSTER_DETAIL_ANALYSIS',
        market_type='KR',
        cluster_id=7001,
        title='반도체주 조정',
        body='기존 상세 본문',
        paragraphs_json=['이 레거시 문단은 재사용하지 않습니다.'],
        target_key='CLUSTER_DETAIL_ANALYSIS:7001',
    )
    selection = AiRetrySelection(
        target=AiSummaryTarget(
            target_key='CLUSTER_DETAIL_ANALYSIS:7001',
            summary_type='CLUSTER_DETAIL_ANALYSIS',
            market_type='KR',
            cluster_id=7001,
        ),
        source_summary=source,
    )
    grounded_sections = [
        {
            'kind': 'impact',
            'title': '시장 영향',
            'paragraphs': [
                {
                    'sentences': [
                        {
                            'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
                            'sourceArticleIds': [1024],
                            'conflictStatus': 'NONE',
                            'conflictingSourceArticleIds': [],
                            'conflictNote': None,
                        }
                    ]
                }
            ],
        }
    ]
    harness = build_mock_gemini_harness(
        monkeypatch,
        [gemini_ai_message({'sections': grounded_sections})],
    )

    class ClusterRepository:
        async def get_cluster_articles(self, cluster_id):
            assert cluster_id == 7001
            return [{'processed_article_id': 1024}]

        async def get_processed_articles(self, article_ids):
            assert article_ids == [1024]
            return [
                {
                    'id': 1024,
                    'canonical_title': '반도체주 약세',
                    'source_summary': '외국인 매도가 이어졌습니다.',
                    'article_body_excerpt': '반도체 업종이 하락했습니다.',
                }
            ]

    payload = await _generate_target(
        selection,
        llm_provider=BatchLlmProvider(harness.client),
        cluster_repo=ClusterRepository(),
        clusters=[
            {
                'id': 7001,
                'market_type': 'KR',
                'title': '반도체주 조정',
                'summary_short': '반도체주가 하락했습니다.',
                'summary_long': '외국인 매도와 업황 우려가 반영됐습니다.',
                'analysis_paragraphs_json': ['낡은 클러스터 문단'],
            }
        ],
        indices=[],
    )
    persisted = _retry_create_params(
        job_id=20,
        business_date=BUSINESS_DATE,
        selection=selection,
        payload=payload,
    )

    assert persisted.prompt_version == 'v2'
    assert persisted.status == 'SUCCESS'
    assert persisted.fallback_used is False
    assert persisted.paragraphs_json == grounded_sections
    assert persisted.metadata_json == {
        'analysisStatus': 'READY',
        'analysisIssues': [],
        'conflictStatus': 'NONE',
        'retry': {'sourceSummaryId': source.summary_id, 'attemptNo': 2},
    }
    assert '이 레거시 문단은 재사용하지 않습니다.' not in repr(persisted)
    assert '낡은 클러스터 문단' not in repr(persisted)


@pytest.mark.anyio
async def test_missing_cluster_detail_retry_persists_fallback_without_page_issue():
    source = replace(
        _source_summary(),
        summary_type='CLUSTER_DETAIL_ANALYSIS',
        market_type='KR',
        cluster_id=7001,
        paragraphs_json=['이 레거시 문단은 재사용하지 않습니다.'],
        target_key='CLUSTER_DETAIL_ANALYSIS:7001',
    )
    selection = AiRetrySelection(
        target=AiSummaryTarget(
            target_key='CLUSTER_DETAIL_ANALYSIS:7001',
            summary_type='CLUSTER_DETAIL_ANALYSIS',
            market_type='KR',
            cluster_id=7001,
        ),
        source_summary=source,
    )

    payload = await _generate_target(
        selection,
        llm_provider=SuccessfulLlm(),
        cluster_repo=FakeClusterRepository(),
        clusters=[],
        indices=[],
    )
    persisted = _retry_create_params(
        job_id=20,
        business_date=BUSINESS_DATE,
        selection=selection,
        payload=payload,
    )

    assert persisted.status == 'FALLBACK'
    assert persisted.fallback_used is True
    assert persisted.paragraphs_json == []
    assert persisted.metadata_json == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'retry': {'sourceSummaryId': source.summary_id, 'attemptNo': 2},
    }
    assert '이 레거시 문단은 재사용하지 않습니다.' not in repr(persisted)

    persisted_record = await FakeSummaryWriteRepository().upsert_retry_summary(
        persisted
    )
    assert (
        _build_page_issues(
            {'metadata_json': {'issues': []}},
            {selection.target.target_key: persisted_record},
        )
        == []
    )


@pytest.mark.anyio
async def test_retry_keeps_successful_headline_when_key_points_fail():
    source = _source_summary()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=KeyPointFailureLlm(),
        page_builder=FakePageBuilder(),
    )

    result = await orchestrator.run(20)

    persisted = summary_writer.params[0]
    assert result.counts.success_count == 0
    assert persisted.status == 'SUCCESS'
    assert persisted.fallback_used is False
    assert persisted.title == 'recovered'
    assert persisted.metadata_json['keyPoints'] == []
    assert persisted.metadata_json['keyPointIssue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }
    assert persisted.metadata_json['retry'] == {
        'sourceSummaryId': source.summary_id,
        'attemptNo': 2,
    }


@pytest.mark.anyio
async def test_retry_key_point_only_preserves_headline_and_recovers_key_points():
    source = replace(
        _source_summary(),
        status='SUCCESS',
        fallback_used=False,
        title='persisted headline',
        body='persisted body',
        metadata_json={
            'keyPoints': [],
            'keyPointIssue': {
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': 'untrusted persisted text',
            },
        },
    )
    provider = KeyPointOnlyLlm()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=provider,
        page_builder=FakePageBuilder(),
    )

    result = await orchestrator.run(20)

    persisted = summary_writer.params[0]
    assert result.counts.recovered_count == 1
    assert persisted.title == 'persisted headline'
    assert persisted.body == 'persisted body'
    assert persisted.metadata_json['keyPoints'] == KEY_POINTS
    assert persisted.metadata_json['keyPointIssue'] is None
    assert provider.headline_calls == 0
    assert provider.key_point_calls == 1


@pytest.mark.anyio
async def test_retry_key_point_only_failure_preserves_headline_and_canonical_issue():
    source = replace(
        _source_summary(),
        status='SUCCESS',
        fallback_used=False,
        title='persisted headline',
        body='persisted body',
        metadata_json={
            'keyPoints': [],
            'keyPointIssue': {
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': 'untrusted persisted text',
            },
        },
    )
    provider = KeyPointOnlyLlm(fail=True)
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=provider,
        page_builder=FakePageBuilder(),
    )

    result = await orchestrator.run(20)

    persisted = summary_writer.params[0]
    assert result.status == 'PARTIAL'
    assert persisted.status == 'SUCCESS'
    assert persisted.fallback_used is False
    assert persisted.title == 'persisted headline'
    assert persisted.body == 'persisted body'
    assert persisted.metadata_json['keyPointIssue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }
    assert provider.headline_calls == 0
    assert provider.key_point_calls == 1


@pytest.mark.anyio
async def test_crash_resume_unresolved_keypoint_retry_does_not_rebuild_page():
    source = replace(
        _source_summary(),
        status='SUCCESS',
        fallback_used=False,
        title='persisted headline',
        body='persisted body',
        metadata_json={
            'keyPointIssue': {
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
            }
        },
    )
    current_retry = replace(
        source,
        summary_id=2,
        batch_job_id=20,
        source_summary_id=source.summary_id,
        attempt_no=2,
    )
    page_builder = FakePageBuilder()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source, current_retry],
        llm=KeyPointOnlyLlm(fail=True),
        page_builder=page_builder,
    )

    result = await orchestrator.run(20)

    assert result.status == 'PARTIAL'
    assert result.counts.recovered_count == 0
    assert result.page is None
    assert page_builder.calls == []
    assert summary_writer.params[0].metadata_json['keyPointIssue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }


@pytest.mark.anyio
async def test_retry_keeps_key_points_when_headline_fails():
    source = _source_summary()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source],
        llm=TimeoutLlm(),
        page_builder=FakePageBuilder(),
    )

    result = await orchestrator.run(20)

    persisted = summary_writer.params[0]
    assert result.counts.success_count == 0
    assert result.counts.recovered_count == 0
    assert persisted.status == 'FALLBACK'
    assert persisted.fallback_used is True
    assert persisted.metadata_json['keyPoints'] == KEY_POINTS
    assert persisted.metadata_json['keyPointIssue'] is None
    assert persisted.metadata_json['retry'] == {
        'sourceSummaryId': source.summary_id,
        'attemptNo': 2,
    }


@pytest.mark.anyio
async def test_zero_recovery_creates_no_page_and_completes_partial():
    page_builder = FakePageBuilder()
    orchestrator, retry_repo, _, _ = _orchestrator(
        lineage=[_source_summary()],
        llm=TimeoutLlm(),
        page_builder=page_builder,
    )

    result = await orchestrator.run(20)

    assert result.status == 'PARTIAL'
    assert result.counts.fallback_count == 1
    assert result.counts.recovered_count == 0
    assert result.page is None
    assert page_builder.calls == []
    assert retry_repo.completed['page_id'] is None


@pytest.mark.anyio
async def test_resume_skips_successful_provider_call_and_builds_missing_page():
    source = _source_summary()
    current_success = replace(
        source,
        summary_id=2,
        batch_job_id=20,
        title='recovered',
        status='SUCCESS',
        fallback_used=False,
        source_summary_id=1,
        attempt_no=2,
    )
    page_builder = FakePageBuilder()
    orchestrator, _, summary_writer, _ = _orchestrator(
        lineage=[source, current_success],
        llm=SuccessfulLlm(),
        page_builder=page_builder,
    )

    result = await orchestrator.run(20)

    assert result.counts.recovered_count == 1
    assert summary_writer.params == []
    assert len(page_builder.calls) == 1


@pytest.mark.anyio
async def test_resume_reuses_checkpointed_page_without_creating_duplicate_version():
    source = _source_summary()
    current_success = replace(
        source,
        summary_id=2,
        batch_job_id=20,
        title='recovered',
        status='SUCCESS',
        fallback_used=False,
        source_summary_id=1,
        attempt_no=2,
    )
    page_builder = FakePageBuilder()
    job = _retry_job(
        checkpoint_json={
            'completedSteps': ['AI_RETRY_BUILD_PAGE'],
            'context': {
                'pageId': 777,
                'pageVersionNo': 4,
                'pageStatus': 'READY',
                'pagePartialMessage': None,
            },
        }
    )
    orchestrator, retry_repo, _, _ = _orchestrator(
        lineage=[source, current_success],
        llm=SuccessfulLlm(),
        page_builder=page_builder,
        job=job,
    )

    result = await orchestrator.run(20)

    assert result.page is not None
    assert result.page.page_id == 777
    assert page_builder.calls == []
    assert retry_repo.completed['page_id'] == 777


@pytest.mark.anyio
async def test_ai_retry_closes_step_runs_as_succeeded():
    job_repo, orchestrator, lease_token = _build_successful_retry()

    await orchestrator.run(job_id=4001, lease_token=lease_token)

    assert job_repo.finished_step_runs
    assert all(step['status'] == 'SUCCEEDED' for step in job_repo.finished_step_runs)
    assert all(step['error_message'] is None for step in job_repo.finished_step_runs)
    assert all(step['error_log'] is None for step in job_repo.finished_step_runs)
    assert len(job_repo.finished_step_runs) == job_repo.step_run_seq


@pytest.mark.anyio
async def test_ai_retry_closes_step_run_as_failed_on_mid_step_exception():
    session, orchestrator, lease_token = _build_failing_retry()

    with pytest.raises(RuntimeError, match='lineage lookup failed'):
        await orchestrator.run(job_id=4002, lease_token=lease_token)

    assert session.committed_steps == {1: 'FAILED'}
    finished_step = session.finished_step_runs[-1]
    assert finished_step['status'] == 'FAILED'
    assert (
        finished_step['error_message'] == 'AI 재처리 단계 실행 중 오류가 발생했습니다.'
    )
    assert 'RuntimeError' in finished_step['error_log']
    assert 'secret-token' not in finished_step['error_log']
    assert '[REDACTED]' in finished_step['error_log']


@pytest.mark.anyio
async def test_ai_retry_commits_running_step_before_step_work_runs():
    session, orchestrator, lease_token = _build_failing_retry()

    with pytest.raises(RuntimeError, match='lineage lookup failed'):
        await orchestrator.run(job_id=4002, lease_token=lease_token)

    begin_index = session.operations.index(('begin_step', AI_RETRY_SELECT_STEP, 1))
    commit_index = session.operations.index(('commit',))
    work_index = session.operations.index(('list_retry_lineage_summaries', 10))
    assert begin_index < commit_index < work_index


@pytest.mark.anyio
async def test_ai_retry_checkpoint_loss_after_finish_closes_durable_step_failed():
    session, orchestrator, lease_token = _build_transactional_successful_retry(
        checkpoint_results=[True, False]
    )

    with pytest.raises(BatchLeaseLostError, match='checkpointing'):
        await orchestrator.run(job_id=4003, lease_token=lease_token)

    failed_step = session.committed_step_runs[2]
    assert failed_step['status'] == 'FAILED'
    assert failed_step['error_message'] == (
        'AI 재처리 단계 실행 중 오류가 발생했습니다.'
    )
    assert 'BatchLeaseLostError' in failed_step['error_log']


@pytest.mark.anyio
async def test_ai_retry_one_shot_success_commit_failure_closes_durable_step_failed():
    session, orchestrator, lease_token = _build_transactional_successful_retry(
        checkpoint_results=[True],
        fail_commit_on={2},
    )

    with pytest.raises(RuntimeError, match='commit failed'):
        await orchestrator.run(job_id=4003, lease_token=lease_token)

    failed_step = session.committed_step_runs[1]
    assert failed_step['status'] == 'FAILED'
    assert failed_step['error_message'] == (
        'AI 재처리 단계 실행 중 오류가 발생했습니다.'
    )
    assert 'RuntimeError' in failed_step['error_log']
    assert 'secret-token' not in failed_step['error_log']
    assert '[REDACTED]' in failed_step['error_log']
