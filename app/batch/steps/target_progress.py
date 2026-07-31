from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from app.batch.exceptions import BatchLeaseLostError
from app.batch.models import BatchExecutionContext

_TARGET_PROGRESS_KEY = 'targetProgress'


@dataclass(frozen=True, slots=True)
class TargetCall:
    target_key: str
    invoke: Callable[[], Awaitable[dict[str, Any]]]


class DurableTargetProgress:
    """Persist fine-grained step targets in the batch job checkpoint."""

    def __init__(
        self,
        *,
        repository: Any,
        job_id: int,
        step_code: str,
        checkpoint: dict[str, Any],
        lease_token: UUID | None,
    ) -> None:
        self._repository = repository
        self._job_id = job_id
        self._step_code = step_code
        self._checkpoint = checkpoint
        self._lease_token = lease_token
        raw_progress = checkpoint.get(_TARGET_PROGRESS_KEY)
        progress = raw_progress if isinstance(raw_progress, dict) else {}
        raw_targets = progress.get(step_code)
        self.completed_targets = {
            target for target in (raw_targets or []) if isinstance(target, str)
        }

    @classmethod
    async def load(
        cls,
        repository: Any,
        *,
        job_id: int,
        step_code: str,
    ) -> DurableTargetProgress:
        checkpoint: dict[str, Any] = {}
        lease_token: UUID | None = None
        get_job = getattr(repository, 'get_job_by_id', None)
        if callable(get_job):
            typed_get_job = cast(Callable[[int], Awaitable[Any]], get_job)
            job = await typed_get_job(job_id)
            if job is not None:
                raw_checkpoint = getattr(job, 'checkpoint_json', None)
                if isinstance(raw_checkpoint, dict):
                    checkpoint = deepcopy(raw_checkpoint)
                candidate_lease = getattr(job, 'lease_token', None)
                if isinstance(candidate_lease, UUID):
                    lease_token = candidate_lease
        return cls(
            repository=repository,
            job_id=job_id,
            step_code=step_code,
            checkpoint=checkpoint,
            lease_token=lease_token,
        )

    async def commit_target(
        self,
        target_key: str,
        context: BatchExecutionContext,
    ) -> None:
        self.completed_targets.add(target_key)
        if self._lease_token is not None:
            raw_progress = self._checkpoint.get(_TARGET_PROGRESS_KEY)
            progress = dict(raw_progress) if isinstance(raw_progress, dict) else {}
            progress[self._step_code] = sorted(self.completed_targets)
            self._checkpoint[_TARGET_PROGRESS_KEY] = progress
            self._checkpoint['context'] = context.to_checkpoint()
            save_checkpoint = cast(
                Callable[..., Awaitable[bool]],
                self._repository.save_checkpoint,
            )
            saved = await save_checkpoint(
                job_id=self._job_id,
                lease_token=self._lease_token,
                current_step=self._step_code,
                checkpoint_json=self._checkpoint,
            )
            if not saved:
                raise BatchLeaseLostError(
                    f'Lease lost while saving {self._step_code} target progress.'
                )
        commit = getattr(self._repository, 'commit', None)
        if callable(commit):
            typed_commit = cast(Callable[[], Awaitable[None]], commit)
            await typed_commit()


async def run_target_calls(
    calls: list[TargetCall],
    *,
    on_result: Callable[[str, dict[str, Any]], Awaitable[None]],
) -> None:
    """Run targets concurrently while draining every sibling before failure."""
    tasks = {
        asyncio.ensure_future(call.invoke()): (index, call.target_key)
        for index, call in enumerate(calls)
    }
    pending = set(tasks)
    try:
        while pending:
            done, pending = await asyncio.wait(
                pending,
                return_when=asyncio.FIRST_COMPLETED,
            )
            results, failures = _collect_task_results(done, tasks)
            if failures:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
                drained_results, drained_failures = _collect_task_results(
                    pending,
                    tasks,
                )
                results.extend(drained_results)
                failures.extend(drained_failures)
                pending.clear()

            for _index, target_key, payload in sorted(results):
                await on_result(target_key, payload)
            if failures:
                raise min(failures, key=lambda item: item[0])[1]
    finally:
        unfinished = [task for task in tasks if not task.done()]
        for task in unfinished:
            task.cancel()
        if unfinished:
            await asyncio.gather(*unfinished, return_exceptions=True)


def _collect_task_results(
    tasks: set[asyncio.Task[dict[str, Any]]],
    task_metadata: dict[asyncio.Task[dict[str, Any]], tuple[int, str]],
) -> tuple[
    list[tuple[int, str, dict[str, Any]]],
    list[tuple[int, BaseException]],
]:
    results: list[tuple[int, str, dict[str, Any]]] = []
    failures: list[tuple[int, BaseException]] = []
    for task in tasks:
        index, target_key = task_metadata[task]
        if task.cancelled():
            continue
        exception = task.exception()
        if exception is not None:
            failures.append((index, exception))
            continue
        results.append((index, target_key, task.result()))
    return results, failures


__all__ = [
    'DurableTargetProgress',
    'TargetCall',
    'run_target_calls',
]
