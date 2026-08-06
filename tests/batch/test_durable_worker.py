from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.batch.worker import BatchJobDispatcher, DurableBatchWorker
from app.core.llm import LlmRetryableError
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


def _job(
    job_id: int = 1001,
    *,
    attempt_count: int = 0,
    max_attempts: int = 3,
) -> BatchJobRecord:
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
        attempt_count=attempt_count,
        max_attempts=max_attempts,
    )


@dataclass
class QueueState:
    claims: list[BatchJobRecord] = field(default_factory=list)
    delayed_claim: BatchJobRecord | None = None
    next_action_delays: list[float | None] = field(default_factory=list)
    released: list[tuple[int, UUID, str]] = field(default_factory=list)
    release_options: list[dict] = field(default_factory=list)
    recovery_calls: int = 0
    recovered_job: BatchJobRecord | None = None
    recovery_ready: bool = True
    recovery_done: bool = False
    recovery_error: Exception | None = None
    commits: int = 0


class FakeQueueRepository:
    def __init__(self, state: QueueState):
        self.state = state

    async def recover_expired_claims(self):
        self.state.recovery_calls += 1
        if self.state.recovery_error is not None:
            raise self.state.recovery_error
        if (
            self.state.recovered_job is not None
            and self.state.recovery_ready
            and not self.state.recovery_done
        ):
            self.state.claims.append(self.state.recovered_job)
            self.state.recovery_done = True
            return BatchLeaseRecoveryResult(requeued_count=1, failed_count=0)
        return BatchLeaseRecoveryResult(requeued_count=0, failed_count=0)

    async def claim_next_job(self, **_kwargs):
        return self.state.claims.pop(0) if self.state.claims else None

    async def seconds_until_next_actionable_job(self):
        if not self.state.next_action_delays:
            return None
        delay = self.state.next_action_delays.pop(0)
        self.state.recovery_ready = True
        if delay is not None and self.state.delayed_claim is not None:
            self.state.claims.append(self.state.delayed_claim)
            self.state.delayed_claim = None
        return delay

    async def heartbeat_claim(self, **_kwargs):
        return True

    async def release_failed_claim(
        self,
        *,
        job_id,
        lease_token,
        error_message,
        **kwargs,
    ):
        self.state.released.append((job_id, lease_token, error_message))
        self.state.release_options.append(kwargs)
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
        llm_retry_base_delay_seconds=5.0,
        llm_retry_max_delay_seconds=300.0,
        llm_retry_jitter_ratio=0.2,
        llm_max_retries=2,
    )


def _worker(state: QueueState, dispatcher) -> DurableBatchWorker:
    return DurableBatchWorker(
        session_maker=FakeSessionMaker(),
        dispatcher=dispatcher,
        settings=_settings(),
        worker_id='worker-test',
        repository_factory=lambda _session: FakeQueueRepository(state),
        retry_jitter_random=lambda: 0.5,
    )


@pytest.mark.anyio
async def test_worker_dispatches_claimed_job_and_releases_failed_attempt(caplog):
    caplog.set_level(logging.ERROR, logger='app.batch.worker')
    state = QueueState(claims=[_job()])
    dispatcher = RecordingDispatcher(error=TimeoutError('provider timeout'))

    processed = await _worker(state, dispatcher).run_once()

    assert processed is True
    assert dispatcher.jobs == [1001]
    assert len(state.released) == 1
    assert state.released[0][0] == 1001
    assert state.released[0][2] == 'TimeoutError: provider timeout'
    failure_record = next(
        record
        for record in caplog.records
        if getattr(record, 'batch_event', None) == 'failed'
    )
    assert failure_record.batch_stage == 'DISPATCH'
    assert failure_record.batch_job_id == 1001
    assert failure_record.batch_reference_date == date(2026, 7, 29)
    assert failure_record.batch_exception_class == 'TimeoutError'
    assert failure_record.batch_duration_seconds >= 0
    assert failure_record.batch_traceback
    assert 'provider timeout' not in caplog.text


@pytest.mark.anyio
async def test_worker_persists_retry_after_without_blocking_or_leaking_details(caplog):
    state = QueueState(claims=[_job(attempt_count=1)])
    dispatcher = RecordingDispatcher(error=LlmRetryableError(retry_after_seconds=17.2))
    caplog.set_level(logging.WARNING, logger='app.batch.worker')

    processed = await asyncio.wait_for(
        _worker(state, dispatcher).run_once(),
        timeout=0.1,
    )

    assert processed is True
    assert dispatcher.jobs == [1001]
    assert state.released[0][2] == ('Temporary LLM provider failure; retry scheduled.')
    assert state.release_options[0] == {
        'error_code': 'LLM_TRANSIENT_RETRY',
        'retry_delay_seconds': 18,
    }
    assert 'provider' not in caplog.text.lower()


@pytest.mark.anyio
async def test_worker_uses_exponential_backoff_with_bounded_jitter():
    state = QueueState(claims=[_job(attempt_count=2, max_attempts=4)])
    dispatcher = RecordingDispatcher(error=LlmRetryableError())

    await _worker(state, dispatcher).run_once()

    # 5 * 2^(2-1) plus 10% deterministic jitter.
    assert state.release_options[0]['retry_delay_seconds'] == 11


@pytest.mark.anyio
async def test_worker_final_attempt_reexecutes_in_deterministic_fallback_mode():
    class FinalFallbackDispatcher:
        def __init__(self) -> None:
            self.calls = 0

        async def dispatch(self, job, lease_token):
            _ = (job, lease_token)
            self.calls += 1
            if self.calls == 1:
                raise LlmRetryableError()

    state = QueueState(claims=[_job(attempt_count=3, max_attempts=3)])
    dispatcher = FinalFallbackDispatcher()

    processed = await _worker(state, dispatcher).run_once()

    assert processed is True
    assert dispatcher.calls == 2
    assert state.released == []


@pytest.mark.anyio
async def test_delayed_llm_retry_survives_worker_restart_and_resumes():
    state = QueueState(
        claims=[_job(attempt_count=1)],
        delayed_claim=_job(attempt_count=2),
        next_action_delays=[0.0],
    )
    first_dispatcher = RecordingDispatcher(
        error=LlmRetryableError(retry_after_seconds=0)
    )

    assert await _worker(state, first_dispatcher).run_once() is True
    assert state.release_options[0]['retry_delay_seconds'] == 0

    resumed_dispatcher = RecordingDispatcher()
    resumed_count = await _worker(state, resumed_dispatcher).run_until_idle()

    assert resumed_count == 1
    assert resumed_dispatcher.jobs == [1001]


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
async def test_background_drain_processes_all_available_jobs_until_idle():
    state = QueueState(claims=[_job(1001), _job(1002)])
    dispatcher = RecordingDispatcher()

    processed_count = await _worker(state, dispatcher).run_until_idle()

    assert processed_count == 2
    assert dispatcher.jobs == [1001, 1002]
    assert state.recovery_calls == 3


@pytest.mark.anyio
async def test_background_drain_waits_for_delayed_retry_job():
    state = QueueState(
        delayed_claim=_job(1001),
        next_action_delays=[0.001],
    )
    dispatcher = RecordingDispatcher()

    processed_count = await _worker(state, dispatcher).run_until_idle()

    assert processed_count == 1
    assert dispatcher.jobs == [1001]


@pytest.mark.anyio
async def test_background_drain_logs_queue_failure_without_exception_message(caplog):
    state = QueueState(
        recovery_error=RuntimeError('postgresql://admin:secret@db.example.com/stock'),
    )
    caplog.set_level(logging.ERROR, logger='app.batch.worker')

    processed_count = await _worker(state, RecordingDispatcher()).run_until_idle()

    assert processed_count == 0
    assert 'exception_class=RuntimeError' in caplog.text
    assert 'postgresql://' not in caplog.text
    assert 'secret' not in caplog.text


@pytest.mark.anyio
async def test_startup_drain_waits_for_live_lease_then_recovers_and_dispatches():
    state = QueueState(
        recovered_job=_job(1001),
        recovery_ready=False,
        next_action_delays=[0.001],
    )
    dispatcher = RecordingDispatcher()

    processed_count = await _worker(state, dispatcher).run_until_idle()

    assert processed_count == 1
    assert state.recovery_done is True
    assert state.recovery_calls >= 2
    assert dispatcher.jobs == [1001]


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
        news_collection_factory=lambda: Orchestrator('news-collection'),
    )
    full_job = _job(1001)
    ai_retry_job = _job(1002)
    ai_retry_job.run_mode = 'AI_RETRY'
class NeverActionableRepository:
    """Simulate another worker instance holding the only RUNNING job under
    a lease that keeps getting renewed via heartbeat. In that situation
    ``seconds_until_next_actionable_job`` never returns None, because the
    lease never actually expires -- this worker can never claim it."""

    def __init__(self) -> None:
        self.delay_calls = 0

    async def recover_expired_claims(self):
        return BatchLeaseRecoveryResult(requeued_count=0, failed_count=0)

    async def claim_next_job(self, **_kwargs):
        return None

    async def seconds_until_next_actionable_job(self):
        self.delay_calls += 1
        return 0.01

    async def commit(self):
        return None


@pytest.mark.anyio
async def test_run_until_idle_stops_waiting_on_a_lease_it_can_never_claim():
    """H3 regression: a RUNNING job actively heartbeated by another worker
    instance must not keep this drain loop polling forever."""
    repository = NeverActionableRepository()
    worker = DurableBatchWorker(
        session_maker=FakeSessionMaker(),
        dispatcher=RecordingDispatcher(),
        settings=SimpleNamespace(
            batch_worker_poll_interval_seconds=0.01,
            batch_worker_heartbeat_seconds=30,
            batch_worker_lease_seconds=0.05,
            batch_worker_retry_delay_seconds=0,
            llm_retry_base_delay_seconds=5.0,
            llm_retry_max_delay_seconds=300.0,
            llm_retry_jitter_ratio=0.2,
            llm_max_retries=2,
        ),
        worker_id='worker-test',
        repository_factory=lambda _session: repository,
        retry_jitter_random=lambda: 0.5,
    )

    processed_count = await asyncio.wait_for(worker.run_until_idle(), timeout=2.0)

    assert processed_count == 0
    assert repository.delay_calls > 0


    news_collection_job = _job(1003)
    news_collection_job.run_mode = 'NEWS_COLLECTION'
    lease_token = UUID('00000000-0000-0000-0000-000000000123')

    await dispatcher.dispatch(full_job, lease_token)
    await dispatcher.dispatch(ai_retry_job, lease_token)
    await dispatcher.dispatch(news_collection_job, lease_token)

    assert calls == [
        ('market', 1001, lease_token),
        ('ai-retry', 1002, lease_token),
        ('news-collection', 1003, lease_token),
    ]
