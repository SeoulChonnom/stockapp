from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class CreateJobStep(BatchStep):
    step_code = "CREATE_JOB"
    started_message = "Batch execution context acknowledged."
    completed_message = "Batch execution context is ready."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("Execution context initialized.")
        return context


__all__ = ["CreateJobStep"]
