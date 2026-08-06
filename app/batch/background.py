from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import lru_cache

from app.batch.logging import log_safe_exception
from app.batch.worker import DurableBatchWorker

LOGGER = logging.getLogger(__name__)


class InProcessBatchScheduler:
    """Manage one finite durable-queue drain inside an API process."""

    def __init__(
        self,
        worker_factory: Callable[[], DurableBatchWorker] | None = None,
    ) -> None:
        self._worker_factory = worker_factory or DurableBatchWorker
        self._tasks: set[asyncio.Task[int]] = set()
        self._drain_requested = False
        self._shutting_down = False

    def start_drain(self) -> None:
        """Start a detached drain, coalescing concurrent scheduling requests."""
        if self._shutting_down:
            return
        running_loop = asyncio.get_running_loop()
        live_tasks: set[asyncio.Task[int]] = set()
        for task in self._tasks:
            if task.done():
                continue
            if task.get_loop() is not running_loop:
                LOGGER.warning(
                    'Discarding background batch drain task bound to a stale '
                    'event loop.'
                )
                continue
            live_tasks.add(task)
        self._tasks = live_tasks
        if self._tasks:
            self._drain_requested = True
            return
        self._drain_requested = False
        task = asyncio.create_task(
            self._worker_factory().run_until_idle(),
            name='batch-background-drain',
        )
        self._tasks.add(task)
        task.add_done_callback(self._drain_finished)

    async def shutdown(self) -> None:
        """Cancel and await active drains before the API event loop closes."""
        self._shutting_down = True
        self._drain_requested = False
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._shutting_down = False

    def _drain_finished(self, task: asyncio.Task[int]) -> None:
        self._tasks.discard(task)
        exception = None if task.cancelled() else task.exception()
        if exception is not None:
            log_safe_exception(
                LOGGER,
                logging.ERROR,
                'Background batch drain task failed.',
                exception=exception,
            )
        if self._drain_requested and not self._tasks and not self._shutting_down:
            self.start_drain()


@lru_cache
def get_in_process_batch_scheduler() -> InProcessBatchScheduler:
    return InProcessBatchScheduler()


__all__ = ['InProcessBatchScheduler', 'get_in_process_batch_scheduler']
