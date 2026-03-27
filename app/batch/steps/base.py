from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository


class BatchStep:
    step_code = "BASE_STEP"
    started_message = "Step started."
    completed_message = "Step completed."

    async def execute(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        await repository.add_event(
            job_id=context.job_id,
            step_code=self.step_code,
            level=EventLevel.INFO.value,
            message=self.started_message,
        )
        updated_context = await self.run(repository, context)
        await repository.add_event(
            job_id=updated_context.job_id,
            step_code=self.step_code,
            level=EventLevel.INFO.value,
            message=self.completed_message,
        )
        return updated_context

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        return context


__all__ = ["BatchStep"]
