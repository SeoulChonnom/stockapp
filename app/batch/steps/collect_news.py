from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class CollectNewsStep(BatchStep):
    step_code = "COLLECT_NEWS"
    started_message = "Collect news step started."
    completed_message = "Collect news step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("News collection step is scaffolded.")
        return context


__all__ = ["CollectNewsStep"]
