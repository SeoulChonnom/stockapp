from __future__ import annotations

import asyncio
import importlib
import logging
import math
import os
import random
import socket
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from functools import partial
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.batch.exceptions import BatchLeaseLostError
from app.batch.logging import log_batch_lifecycle, log_safe_exception
from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator
from app.batch.orchestrators.news_collection import NaverNewsCollectionOrchestrator
from app.core.llm import LlmRetryableError, llm_retry_exhausted_mode
from app.core.settings import Settings, get_settings
from app.db.enums import BatchRunMode
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.projections import BatchJobRecord
from app.db.session import get_session_maker

LOGGER = logging.getLogger(__name__)


class JobDispatcher(Protocol):
    async def dispatch(self, job: BatchJobRecord, lease_token: UUID) -> None:
        """Execute one claimed job."""


class BatchJobDispatcher:
    """Route durable queue jobs to their run-mode orchestrator."""

    def __init__(
        self,
        *,
        market_daily_factory: Callable[[], Any] | None = None,
        ai_retry_factory: Callable[[], Any] | None = None,
        news_collection_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._market_daily_factory = (
            market_daily_factory or MarketDailyBatchOrchestrator
        )
        self._ai_retry_factory = ai_retry_factory
        self._news_collection_factory = (
            news_collection_factory or NaverNewsCollectionOrchestrator
        )

    async def dispatch(self, job: BatchJobRecord, lease_token: UUID) -> None:
        if job.run_mode in {
            BatchRunMode.FULL.value,
            BatchRunMode.PAGE_REBUILD.value,
        }:
            await self._market_daily_factory().run(
                job.job_id,
                lease_token=lease_token,
            )
            return
        if job.run_mode == BatchRunMode.AI_RETRY.value:
            await self._build_ai_retry_orchestrator().run(
                job.job_id,
                lease_token=lease_token,
            )
            return
        if job.run_mode == BatchRunMode.NEWS_COLLECTION.value:
            await self._news_collection_factory().run(
                job.job_id,
                lease_token=lease_token,
            )
            return
        raise RuntimeError(f'Unsupported batch run mode: {job.run_mode}')

    def _build_ai_retry_orchestrator(self) -> Any:
        if self._ai_retry_factory is not None:
            return self._ai_retry_factory()
        module = importlib.import_module('app.batch.ai_retry')
        orchestrator_type = module.AiRetryOrchestrator
        return orchestrator_type()


class DurableBatchWorker:
    """Claim, heartbeat, dispatch, and retry PostgreSQL-backed batch jobs."""

    def __init__(
        self,
        *,
        session_maker: async_sessionmaker | None = None,
        dispatcher: JobDispatcher | None = None,
        settings: Settings | None = None,
        worker_id: str | None = None,
        repository_factory: Callable[[Any], Any] | None = None,
        retry_jitter_random: Callable[[], float] = random.random,
    ) -> None:
        self._session_maker = session_maker or get_session_maker()
        self._dispatcher = dispatcher or BatchJobDispatcher()
        self._settings = settings or get_settings()
        self.worker_id = worker_id or _default_worker_id()
        self._repository_factory = repository_factory or BatchJobRepository
        self._retry_jitter_random = retry_jitter_random

    async def run_forever(self) -> None:
        """Continuously process available jobs until the process is stopped."""
        LOGGER.info('Durable batch worker started: worker_id=%s', self.worker_id)
        while True:
            try:
                processed = await self.run_once()
            except Exception as exc:
                log_safe_exception(
                    LOGGER,
                    logging.ERROR,
                    'Durable batch worker queue operation failed.',
                    exception=exc,
                )
                processed = False
            if not processed:
                await asyncio.sleep(self._settings.batch_worker_poll_interval_seconds)

    async def run_until_idle(self) -> int:
        """Drain jobs and wait for delayed retries or live leases to become actionable."""
        processed_count = 0
        wait_started_at: float | None = None
        max_wait_seconds = self._settings.batch_worker_lease_seconds
        while True:
            try:
                processed = await self.run_once()
            except Exception as exc:
                log_safe_exception(
                    LOGGER,
                    logging.ERROR,
                    'Background batch queue drain failed.',
                    exception=exc,
                )
                return processed_count
            if processed:
                processed_count += 1
                wait_started_at = None
                continue

            try:
                delay_seconds = await self._seconds_until_next_actionable_job()
            except Exception as exc:
                log_safe_exception(
                    LOGGER,
                    logging.ERROR,
                    'Background batch next-action lookup failed.',
                    exception=exc,
                )
                return processed_count
            if delay_seconds is None:
                return processed_count
            # A RUNNING job whose lease is actively renewed by another
            # worker instance never surfaces as None here, so bound how
            # long this drain will keep polling for it -- otherwise it
            # would never finish. Giving up leaves the job to whichever
            # worker holds it; any other actionable work is picked up by
            # the next drain (next batch request or app startup).
            if wait_started_at is None:
                wait_started_at = perf_counter()
            elif perf_counter() - wait_started_at >= max_wait_seconds:
                return processed_count
            poll_seconds = self._settings.batch_worker_poll_interval_seconds
            await asyncio.sleep(
                poll_seconds if delay_seconds <= 0 else min(delay_seconds, poll_seconds)
            )

    async def run_once(self) -> bool:
        """Recover expired leases and process at most one available job."""
        await self._recover_expired_claims()
        lease_token = uuid4()
        job = await self._claim_next_job(lease_token)
        if job is None:
            return False
        attempt_started_at = perf_counter()
        log_dispatch_event = partial(
            log_batch_lifecycle,
            LOGGER,
            job_id=job.job_id,
            page_id=_job_page_id(job),
            reference_date=job.business_date,
            stage='DISPATCH',
        )
        log_dispatch_event(logging.INFO, event='started')
        try:
            await self._dispatch_with_llm_retry_policy(job, lease_token)
        except BatchLeaseLostError as exc:
            log_dispatch_event(
                logging.WARNING,
                event='failed',
                duration_seconds=perf_counter() - attempt_started_at,
                exception=exc,
            )
        except LlmRetryableError as exc:
            log_dispatch_event(
                logging.WARNING,
                event='retry_scheduled',
                duration_seconds=perf_counter() - attempt_started_at,
                exception=exc,
            )
            await self._release_failed_claim(
                job,
                lease_token,
                exc,
                error_code='LLM_TRANSIENT_RETRY',
                error_message='Temporary LLM provider failure; retry scheduled.',
                retry_delay_seconds=self._llm_retry_delay_seconds(job, exc),
            )
        except Exception as exc:
            log_dispatch_event(
                logging.ERROR,
                event='failed',
                duration_seconds=perf_counter() - attempt_started_at,
                exception=exc,
            )
            await self._release_failed_claim(job, lease_token, exc)
        else:
            log_dispatch_event(
                logging.INFO,
                event='completed',
                duration_seconds=perf_counter() - attempt_started_at,
            )
        return True

    @asynccontextmanager
    async def _repository(self) -> AsyncIterator[Any]:
        async with self._session_maker() as session:
            yield self._repository_factory(session)

    async def _recover_expired_claims(self) -> None:
        async with self._repository() as repository:
            result = await repository.recover_expired_claims()
            await repository.commit()
        if result.requeued_count or result.failed_count:
            LOGGER.warning(
                'Recovered expired leases: requeued=%s failed=%s',
                result.requeued_count,
                result.failed_count,
            )

    async def _claim_next_job(self, lease_token: UUID) -> BatchJobRecord | None:
        async with self._repository() as repository:
            job = await repository.claim_next_job(
                worker_id=self.worker_id,
                lease_token=lease_token,
                lease_seconds=self._settings.batch_worker_lease_seconds,
            )
            await repository.commit()
            return job

    async def _seconds_until_next_actionable_job(self) -> float | None:
        async with self._repository() as repository:
            return await repository.seconds_until_next_actionable_job()

    async def _heartbeat(self, job_id: int, lease_token: UUID) -> bool:
        async with self._repository() as repository:
            renewed = await repository.heartbeat_claim(
                job_id=job_id,
                worker_id=self.worker_id,
                lease_token=lease_token,
                lease_seconds=self._settings.batch_worker_lease_seconds,
            )
            await repository.commit()
            return renewed

    async def _release_failed_claim(
        self,
        job: BatchJobRecord,
        lease_token: UUID,
        exc: Exception,
        *,
        error_code: str = 'BATCH_ATTEMPT_FAILED',
        error_message: str | None = None,
        retry_delay_seconds: int | None = None,
    ) -> None:
        async with self._repository() as repository:
            await repository.release_failed_claim(
                job_id=job.job_id,
                lease_token=lease_token,
                error_code=error_code,
                error_message=error_message or f'{type(exc).__name__}: {exc}',
                retry_delay_seconds=(
                    self._settings.batch_worker_retry_delay_seconds
                    if retry_delay_seconds is None
                    else retry_delay_seconds
                ),
            )
            await repository.commit()

    async def _dispatch_with_llm_retry_policy(
        self,
        job: BatchJobRecord,
        lease_token: UUID,
    ) -> None:
        try:
            await self._dispatch_with_heartbeat(job, lease_token)
        except LlmRetryableError:
            llm_attempt_limit = min(
                job.max_attempts,
                self._settings.llm_max_retries + 1,
            )
            if job.attempt_count < llm_attempt_limit:
                raise
            with llm_retry_exhausted_mode():
                await self._dispatch_with_heartbeat(job, lease_token)

    def _llm_retry_delay_seconds(
        self,
        job: BatchJobRecord,
        exc: LlmRetryableError,
    ) -> int:
        if exc.retry_after_seconds is not None:
            return max(0, math.ceil(exc.retry_after_seconds))
        base_delay = self._settings.llm_retry_base_delay_seconds * (
            2 ** max(job.attempt_count - 1, 0)
        )
        capped_delay = min(base_delay, self._settings.llm_retry_max_delay_seconds)
        jitter = (
            capped_delay
            * self._settings.llm_retry_jitter_ratio
            * self._retry_jitter_random()
        )
        return max(0, math.ceil(capped_delay + jitter))

    async def _dispatch_with_heartbeat(
        self,
        job: BatchJobRecord,
        lease_token: UUID,
    ) -> None:
        stop_heartbeat = asyncio.Event()
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(
                job.job_id,
                lease_token,
                stop_heartbeat=stop_heartbeat,
                lease_lost=lease_lost,
            )
        )
        dispatch_task = asyncio.create_task(self._dispatcher.dispatch(job, lease_token))
        lease_lost_task = asyncio.create_task(lease_lost.wait())
        try:
            done, _pending = await asyncio.wait(
                {dispatch_task, lease_lost_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if dispatch_task in done:
                await dispatch_task
                return
            if lease_lost_task in done and lease_lost.is_set():
                dispatch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatch_task
                raise BatchLeaseLostError(
                    f'Worker lease was lost for batch job {job.job_id}.'
                )
            await dispatch_task
        finally:
            stop_heartbeat.set()
            if not dispatch_task.done():
                dispatch_task.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatch_task
            lease_lost_task.cancel()
            with suppress(asyncio.CancelledError):
                await lease_lost_task
            with suppress(asyncio.CancelledError):
                await heartbeat_task

    async def _heartbeat_loop(
        self,
        job_id: int,
        lease_token: UUID,
        *,
        stop_heartbeat: asyncio.Event,
        lease_lost: asyncio.Event,
    ) -> None:
        while not stop_heartbeat.is_set():
            try:
                await asyncio.wait_for(
                    stop_heartbeat.wait(),
                    timeout=self._settings.batch_worker_heartbeat_seconds,
                )
            except TimeoutError:
                try:
                    renewed = await self._heartbeat(job_id, lease_token)
                except Exception as exc:
                    log_safe_exception(
                        LOGGER,
                        logging.ERROR,
                        f'Batch worker heartbeat failed: job_id={job_id}.',
                        exception=exc,
                    )
                    lease_lost.set()
                    return
                if not renewed:
                    lease_lost.set()
                    return


def _default_worker_id() -> str:
    return f'{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}'


def _job_page_id(job: BatchJobRecord) -> int | None:
    return job.page_id or getattr(job, 'source_page_id', None)


async def _run() -> None:
    await DurableBatchWorker().run_forever()


def main() -> None:
    """Run the durable batch worker process."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == '__main__':
    main()
