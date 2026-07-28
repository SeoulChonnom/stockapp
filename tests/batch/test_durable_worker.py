from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.batch.worker import BatchJobDispatcher, DurableBatchWorker
from app.db.repositories.projections import (
    BatchJobRecord,
    BatchLeaseRecoveryResult,
)


class FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        _ = (exc_type, exc, tb)
        return False


class FakeSessionMaker:
    def __call__(self):
        return FakeSession()


def _job(job_id: int = 1001) -> BatchJobRecord:
    now = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
    return BatchJobRecord(
        job_id=job_id,
        job_name='market_daily_batch',
        business_date=date(2026, 7, 29),
        status='RUNNING',
        started_at=now,
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='FULL',
    )


@dataclass
class QueueState:
    claims: list[BatchJobRecord] = field(default_factory=list)
    released: list[tuple[int, UUID, str]] = field(default_factory=list)
    recovery_calls: int = 0
    recovered_job: BatchJobRecord | None = None
    recovery_done: bool = False
    commits: int = 0


class FakeQueueRepository:
    def __init__(self, state: QueueState):
        self.state = state

    async def recover_expired_claims(self):
        self.state.recovery_calls += 1
        if self.state.recovered_job is not None and not self.state.recovery_done:
            self.state.claims.append(self.state.recovered_job)
            self.state.recovery_done = True
            return BatchLeaseRecoveryResult(requeued_count=1, failed_count=0)
        return BatchLeaseRecoveryResult(requeued_count=0, failed_count=0)

    async def claim_next_job(self, **_kwargs):
        return self.state.claims.pop(0) if self.state.claims else None

    async def heartbeat_claim(self, **_kwargs):
        return True

    async def release_failed_claim(
        self,
        *,
        job_id,
        lease_token,
        error_message,
        **_kwargs,
    ):
        self.state.released.append((job_id, lease_token, error_message))
        return 'PENDING'

    async def commit(self):
        self.state.commits += 1


class RecordingDispatcher:
    def __init__(self, *, error: Exception | None = None):
        self.error = error
        self.jobs: list[int] = []

    async def dispatch(self, job, lease_token):
        _ = lease_token
        self.jobs.append(job.job_id)
        if self.error is not None:
            raise self.error


class BlockingDispatcher:
    def __init__(self):
        self.started = asyncio.Event()

    async def dispatch(self, job, lease_token):
        _ = (job, lease_token)
        self.started.set()
        await asyncio.Event().wait()


def _settings():
    return SimpleNamespace(
        batch_worker_poll_interval_seconds=0.01,
        batch_worker_heartbeat_seconds=30,
        batch_worker_lease_seconds=120,
        batch_worker_retry_delay_seconds=0,
    )


def _worker(state: QueueState, dispatcher) -> DurableBatchWorker:
    return DurableBatchWorker(
        session_maker=FakeSessionMaker(),
        dispatcher=dispatcher,
        settings=_settings(),
        worker_id='worker-test',
        repository_factory=lambda _session: FakeQueueRepository(state),
    )


@pytest.mark.anyio
async def test_worker_dispatches_claimed_job_and_releases_failed_attempt():
    state = QueueState(claims=[_job()])
    dispatcher = RecordingDispatcher(error=TimeoutError('provider timeout'))

    processed = await _worker(state, dispatcher).run_once()

    assert processed is True
    assert dispatcher.jobs == [1001]
    assert len(state.released) == 1
    assert state.released[0][0] == 1001
    assert state.released[0][2] == 'TimeoutError: provider timeout'


@pytest.mark.anyio
async def test_process_restart_leaves_claim_for_lease_recovery_and_resume():
    original_job = _job()
    state = QueueState(claims=[original_job])
    blocking_dispatcher = BlockingDispatcher()
    first_worker = _worker(state, blocking_dispatcher)

    first_attempt = asyncio.create_task(first_worker.run_once())
    await blocking_dispatcher.started.wait()
    first_attempt.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_attempt

    assert state.released == []
    state.recovered_job = original_job
    second_dispatcher = RecordingDispatcher()

    processed = await _worker(state, second_dispatcher).run_once()

    assert processed is True
    assert state.recovery_done is True
    assert second_dispatcher.jobs == [1001]


@pytest.mark.anyio
async def test_dispatcher_routes_full_and_ai_retry_run_modes():
    calls: list[tuple[str, int, UUID]] = []

    class Orchestrator:
        def __init__(self, name: str):
            self.name = name

        async def run(self, job_id, lease_token=None):
            calls.append((self.name, job_id, lease_token))

    dispatcher = BatchJobDispatcher(
        market_daily_factory=lambda: Orchestrator('market'),
        ai_retry_factory=lambda: Orchestrator('ai-retry'),
    )
    full_job = _job(1001)
    ai_retry_job = _job(1002)
    ai_retry_job.run_mode = 'AI_RETRY'
    lease_token = UUID('00000000-0000-0000-0000-000000000123')

    await dispatcher.dispatch(full_job, lease_token)
    await dispatcher.dispatch(ai_retry_job, lease_token)

    assert calls == [
        ('market', 1001, lease_token),
        ('ai-retry', 1002, lease_token),
    ]
