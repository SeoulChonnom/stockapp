from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.policies import determine_batch_status
from app.batch.steps.base import BatchStep
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository


class FinalizeJobStep(BatchStep):
    step_code = 'FINALIZE_JOB'
    started_message = 'Finalize job step started.'
    completed_message = 'Finalize job step completed.'

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if not context.partial_message and context.partial_reasons:
            context.partial_message = '; '.join(context.partial_reasons[:3])
        status = determine_batch_status(context)
        log_summary = ' '.join(context.log_messages) if context.log_messages else None
        await repository.mark_job_completed(
            job_id=context.job_id,
            status=status,
            raw_news_count=context.raw_news_count,
            processed_news_count=context.processed_news_count,
            cluster_count=context.cluster_count,
            page_id=context.page_id,
            page_version_no=context.page_version_no,
            partial_message=context.partial_message,
            error_code=context.error_code,
            error_message=context.error_message,
            log_summary=log_summary,
        )
        await repository.add_event(
            job_id=context.job_id,
            step_code=self.step_code,
            level=EventLevel.INFO.value,
            message=f'Batch finalized with status={status}.',
        )
        return context


__all__ = ['FinalizeJobStep']
