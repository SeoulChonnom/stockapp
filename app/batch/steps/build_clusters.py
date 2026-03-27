from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class BuildClustersStep(BatchStep):
    step_code = "BUILD_CLUSTERS"
    started_message = "Build clusters step started."
    completed_message = "Build clusters step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("Cluster building step is scaffolded.")
        return context


__all__ = ["BuildClustersStep"]
