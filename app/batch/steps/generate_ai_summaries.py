from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class GenerateAiSummariesStep(BatchStep):
    step_code = "GENERATE_AI_SUMMARIES"
    started_message = "Generate AI summaries step started."
    completed_message = "Generate AI summaries step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("AI summary generation step is scaffolded.")
        return context


__all__ = ["GenerateAiSummariesStep"]
