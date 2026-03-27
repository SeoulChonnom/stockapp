from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class BuildPageSnapshotStep(BatchStep):
    step_code = "BUILD_PAGE_SNAPSHOT"
    started_message = "Build page snapshot step started."
    completed_message = "Build page snapshot step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("Page snapshot build step is scaffolded.")
        return context


__all__ = ["BuildPageSnapshotStep"]
