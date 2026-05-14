from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timezone

import pytest

from tests.support import DummyResult, RecordingAsyncSession, load_module

orchestrator_module = load_module('app.batch.orchestrators.market_daily')
projections_module = load_module('app.db.repositories.projections')

MarketDailyBatchOrchestrator = orchestrator_module.MarketDailyBatchOrchestrator
BatchJobRecord = projections_module.BatchJobRecord


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class FakeSessionMaker:
    def __call__(self):
        return FakeSession()


@dataclass
class FakeRepository:
    events: list[tuple[str, str]]
    completed_statuses: list[str]
    session: RecordingAsyncSession

    async def get_job_by_id(self, job_id: int):
        return BatchJobRecord(
            job_id=job_id,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='RUNNING',
            started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
            ended_at=None,
            duration_seconds=None,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            force_run=False,
            rebuild_page_only=False,
        )

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        _ = kwargs
        self.events.append((step_code, message))

    async def mark_job_completed(self, *, status: str, **kwargs):
        _ = kwargs
        self.completed_statuses.append(status)

    async def mark_job_failed(self, *, error_code: str, error_message: str, **kwargs):
        self.completed_statuses.append('FAILED')
        self.events.append(('FAILED', f'{error_code}:{error_message}', kwargs))

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()


@pytest.mark.anyio
async def test_market_daily_orchestrator_runs_all_steps_with_explicit_test_doubles(
    monkeypatch,
):
    fake_repository = FakeRepository(
        events=[],
        completed_statuses=[],
        session=RecordingAsyncSession(results=[DummyResult([])]),
    )

    class StubStep:
        def __init__(self, step_code: str):
            self.step_code = step_code

        async def execute(self, repository, context):
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level='INFO',
                message=f'{self.step_code} test double executed.',
            )
            return context

    class BuildPageSnapshotStubStep(StubStep):
        async def execute(self, repository, context):
            context.page_id = 501
            context.page_version_no = 1
            return await super().execute(repository, context)

    monkeypatch.setattr(
        orchestrator_module,
        'BatchJobRepository',
        lambda session: fake_repository,
    )
    orchestrator = MarketDailyBatchOrchestrator(session_maker=FakeSessionMaker())
    orchestrator._steps = [
        orchestrator_module.CreateJobStep(),
        StubStep('COLLECT_NEWS'),
        StubStep('DEDUPE_ARTICLES'),
        StubStep('BUILD_CLUSTERS'),
        StubStep('COLLECT_MARKET_INDICES'),
        StubStep('GENERATE_AI_SUMMARIES'),
        BuildPageSnapshotStubStep('BUILD_PAGE_SNAPSHOT'),
        orchestrator_module.FinalizeJobStep(),
    ]

    await orchestrator.run(1001)

    step_codes = [step_code for step_code, _message in fake_repository.events]
    assert step_codes[0] == 'ORCHESTRATE'
    assert 'COLLECT_NEWS' in step_codes
    assert 'DEDUPE_ARTICLES' in step_codes
    assert 'BUILD_CLUSTERS' in step_codes
    assert 'COLLECT_MARKET_INDICES' in step_codes
    assert 'GENERATE_AI_SUMMARIES' in step_codes
    assert 'BUILD_PAGE_SNAPSHOT' in step_codes
    assert 'FINALIZE_JOB' in step_codes
    assert fake_repository.completed_statuses == ['SUCCESS']


@pytest.mark.anyio
async def test_market_daily_orchestrator_marks_job_failed_when_step_raises(monkeypatch):
    fake_repository = FakeRepository(
        events=[],
        completed_statuses=[],
        session=RecordingAsyncSession(results=[DummyResult([])]),
    )

    class FailingStep:
        async def execute(self, repository, context):
            _ = (repository, context)
            raise TimeoutError('provider timeout')

    monkeypatch.setattr(
        orchestrator_module,
        'BatchJobRepository',
        lambda session: fake_repository,
    )
    orchestrator = MarketDailyBatchOrchestrator(session_maker=FakeSessionMaker())
    orchestrator._steps = [FailingStep()]

    with pytest.raises(TimeoutError, match='provider timeout'):
        await orchestrator.run(1001)

    assert fake_repository.completed_statuses == ['FAILED']
    assert fake_repository.events[-2][0] == 'ORCHESTRATE'
    assert fake_repository.events[-2][1] == 'Market daily batch orchestrator failed.'
    assert fake_repository.events[-1][0] == 'FAILED'
    assert fake_repository.events[-1][1].startswith('INTERNAL_BATCH_ERROR:')
    assert fake_repository.events[-1][2]['job_id'] == 1001


class RecordingSessionContext:
    def __init__(self, session: RecordingAsyncSession):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc, tb)
        if exc_type is not None and self.session.pending_domain_writes:
            await self.session.rollback()
        return False


class RecordingSessionMaker:
    def __init__(self, session: RecordingAsyncSession):
        self.session = session

    def __call__(self):
        return RecordingSessionContext(self.session)


def build_running_job_row(job_id: int = 1001) -> dict:
    return {
        'job_id': job_id,
        'job_name': 'market_daily_batch',
        'business_date': date(2026, 3, 17),
        'status': 'RUNNING',
        'trigger_type': 'MANUAL',
        'triggered_by_user_id': 'USER-0001',
        'force_run': False,
        'rebuild_page_only': False,
        'started_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
        'ended_at': None,
        'duration_seconds': None,
        'market_scope': 'GLOBAL',
        'raw_news_count': 0,
        'processed_news_count': 0,
        'cluster_count': 0,
        'page_id': None,
        'page_version_no': None,
        'partial_message': None,
        'error_code': None,
        'error_message': None,
        'log_summary': None,
        'created_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
        'updated_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
    }


@pytest.mark.anyio
async def test_market_daily_orchestrator_commits_failure_state_after_step_rollback():
    session = RecordingAsyncSession(results=[DummyResult([build_running_job_row()])])

    class FailingDomainWriteStep:
        async def execute(self, repository, context):
            await repository.session.execute('DOMAIN_WRITE:market_index_daily')
            await repository.session.rollback()
            raise TimeoutError('provider timeout')

    orchestrator = MarketDailyBatchOrchestrator(
        session_maker=RecordingSessionMaker(session)
    )
    orchestrator._steps = [FailingDomainWriteStep()]

    with pytest.raises(TimeoutError, match='provider timeout'):
        await orchestrator.run(1001)

    assert session.rolled_back_domain_writes == ['DOMAIN_WRITE:market_index_daily']
    assert session.committed_domain_writes == []
    assert session.rollbacks == 1
    assert session.commits == 3

    event_payloads = [
        params
        for params in session.parameters
        if isinstance(params, dict) and 'step_code' in params
    ]
    assert event_payloads[-1]['level'] == 'ERROR'
    assert event_payloads[-1]['step_code'] == 'ORCHESTRATE'
    assert event_payloads[-1]['message'] == 'Market daily batch orchestrator failed.'

    failed_status_payloads = [
        params
        for params in session.parameters
        if isinstance(params, dict) and params.get('status') == 'FAILED'
    ]
    assert failed_status_payloads[-1]['error_code'] == 'INTERNAL_BATCH_ERROR'
