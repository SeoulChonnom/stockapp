from __future__ import annotations

import asyncio
import importlib
import logging
import os
import socket
from collections.abc import Callable
from contextlib import suppress
from typing import Any, Protocol
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.batch.exceptions import BatchLeaseLostError
from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator
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
    ) -> None:
        self._market_daily_factory = (
            market_daily_factory or MarketDailyBatchOrchestrator
        )
        self._ai_retry_factory = ai_retry_factory

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
    ) -> None:
        self._session_maker = session_maker or get_session_maker()
        self._dispatcher = dispatcher or BatchJobDispatcher()
        self._settings = settings or get_settings()
        self.worker_id = worker_id or _default_worker_id()
        self._repository_factory = repository_factory or BatchJobRepository

    async def run_forever(self) -> None:
        """Continuously process available jobs until the process is stopped."""
        LOGGER.info('Durable batch worker started: worker_id=%s', self.worker_id)
        while True:
            try:
                processed = await self.run_once()
            except Exception:
                LOGGER.exception('Durable batch worker queue operation failed.')
                processed = False
            if not processed:
                await asyncio.sleep(self._settings.batch_worker_poll_interval_seconds)

    async def run_once(self) -> bool:
        """Recover expired leases and process at most one available job."""
        await self._recover_expired_claims()
        lease_token = uuid4()
        job = await self._claim_next_job(lease_token)
        if job is None:
            return False
        try:
            await self._dispatch_with_heartbeat(job, lease_token)
        except BatchLeaseLostError:
            LOGGER.warning(
                'Worker lease lost: worker_id=%s job_id=%s',
                self.worker_id,
                job.job_id,
            )
        except Exception as exc:
            LOGGER.exception('Batch job attempt failed: job_id=%s', job.job_id)
            await self._release_failed_claim(job, lease_token, exc)
        return True

    async def _recover_expired_claims(self) -> None:
        async with self._session_maker() as session:
            repository = self._repository_factory(session)
            result = await repository.recover_expired_claims()
            await repository.commit()
        if result.requeued_count or result.failed_count:
            LOGGER.warning(
                'Recovered expired leases: requeued=%s failed=%s',
                result.requeued_count,
                result.failed_count,
            )

    async def _claim_next_job(self, lease_token: UUID) -> BatchJobRecord | None:
        async with self._session_maker() as session:
            repository = self._repository_factory(session)
            job = await repository.claim_next_job(
                worker_id=self.worker_id,
                lease_token=lease_token,
                lease_seconds=self._settings.batch_worker_lease_seconds,
            )
            await repository.commit()
            return job

    async def _heartbeat(self, job_id: int, lease_token: UUID) -> bool:
        async with self._session_maker() as session:
            repository = self._repository_factory(session)
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
    ) -> None:
        async with self._session_maker() as session:
            repository = self._repository_factory(session)
            await repository.release_failed_claim(
                job_id=job.job_id,
                lease_token=lease_token,
                error_code='BATCH_ATTEMPT_FAILED',
                error_message=f'{type(exc).__name__}: {exc}',
                retry_delay_seconds=(self._settings.batch_worker_retry_delay_seconds),
            )
            await repository.commit()

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
                except Exception:
                    LOGGER.exception('Batch worker heartbeat failed: job_id=%s', job_id)
                    lease_lost.set()
                    return
                if not renewed:
                    lease_lost.set()
                    return


def _default_worker_id() -> str:
    return f'{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}'


async def _run() -> None:
    await DurableBatchWorker().run_forever()


def main() -> None:
    """Run the durable batch worker process."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_run())


if __name__ == '__main__':
    main()
