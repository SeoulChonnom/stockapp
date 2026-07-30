from __future__ import annotations

import asyncio
import logging

import pytest

from app.batch.background import InProcessBatchScheduler


class BlockingWorker:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def run_until_idle(self) -> int:
        self.started.set()
        try:
            await self.release.wait()
        finally:
            self.cancelled.set()
        return 0


@pytest.mark.anyio
async def test_scheduler_detaches_coalesces_and_cancels_queue_drain():
    worker = BlockingWorker()
    factory_calls = 0

    def worker_factory():
        nonlocal factory_calls
        factory_calls += 1
        return worker

    scheduler = InProcessBatchScheduler(worker_factory)

    scheduler.start_drain()
    scheduler.start_drain()
    await worker.started.wait()

    assert factory_calls == 1

    await scheduler.shutdown()

    assert worker.cancelled.is_set()


@pytest.mark.anyio
async def test_scheduler_runs_follow_up_drain_requested_during_active_drain():
    workers: list[BlockingWorker] = []

    def worker_factory():
        worker = BlockingWorker()
        workers.append(worker)
        return worker

    scheduler = InProcessBatchScheduler(worker_factory)

    scheduler.start_drain()
    await workers[0].started.wait()
    scheduler.start_drain()
    workers[0].release.set()

    for _ in range(10):
        await asyncio.sleep(0)
        if len(workers) == 2 and workers[1].started.is_set():
            break

    assert len(workers) == 2
    assert workers[1].started.is_set()

    await scheduler.shutdown()


@pytest.mark.anyio
async def test_scheduler_logs_and_swallows_background_failure(caplog):
    class FailingWorker:
        async def run_until_idle(self) -> int:
            raise RuntimeError('sensitive provider detail')

    caplog.set_level(logging.ERROR, logger='app.batch.background')
    scheduler = InProcessBatchScheduler(FailingWorker)

    scheduler.start_drain()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await scheduler.shutdown()

    assert 'exception_class=RuntimeError' in caplog.text
    assert 'sensitive provider detail' not in caplog.text
