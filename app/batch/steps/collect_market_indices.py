from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.steps.base import BatchStep
from app.db.repositories.batch_job_repo import BatchJobRepository


class CollectMarketIndicesStep(BatchStep):
    step_code = "COLLECT_MARKET_INDICES"
    started_message = "Collect market indices step started."
    completed_message = "Collect market indices step completed."

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        _ = repository
        context.log_messages.append("Market indices collection step is scaffolded.")
        return context


__all__ = ["CollectMarketIndicesStep"]
