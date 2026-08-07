from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from app.batch.ai_retry.models import AiRetryPageResult
from app.batch.ai_retry.orchestrator import AiRetryOrchestrator
from app.db.repositories.ai_retry_repo import AiRetryJob
from app.db.repositories.projections import AiSummaryRecord

BUSINESS_DATE = date(2026, 7, 28)
GENERATED_AT = datetime(2026, 7, 29, tzinfo=UTC)


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
        self.finished_step_runs: list[tuple[int, str]] = []

    async def add_event(self, **kwargs):
        self.events.append(kwargs)

    async def begin_step(self, *, job_id, lease_token, step_code):
        self.begun_steps.append(step_code)
        self.step_run_seq += 1
        return self.step_run_seq

    async def finish_step_run(self, *, step_run_id, status):
        self.finished_step_runs.append((step_run_id, status))
        return True


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
    assert all(
        status == 'SUCCEEDED' for _, status in job_repo.finished_step_runs
    )
    assert len(job_repo.finished_step_runs) == job_repo.step_run_seq
