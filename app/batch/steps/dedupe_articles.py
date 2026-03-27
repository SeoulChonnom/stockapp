from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class DedupeArticlesStep(BatchStep):
    step_code = "DEDUPE_ARTICLES"
    started_message = "Dedupe articles step started."
    completed_message = "Dedupe articles step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("Article deduplication step is scaffolded.")
        return context


__all__ = ["DedupeArticlesStep"]
