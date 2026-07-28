from __future__ import annotations

from copy import deepcopy

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.batch.exceptions import BatchPipelineError
from app.batch.models import BatchExecutionContext
from app.batch.steps import (
    BuildClustersStep,
    BuildPageSnapshotStep,
    CollectMarketIndicesStep,
    CollectNewsStep,
    CreateJobStep,
    DedupeArticlesStep,
    FinalizeJobStep,
    GenerateAiSummariesStep,
)
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.session import get_session_maker


class MarketDailyBatchOrchestrator:
    def __init__(self, session_maker: async_sessionmaker | None = None) -> None:
        self._session_maker = session_maker or get_session_maker()
        self._steps = [
            CreateJobStep(),
            CollectNewsStep(),
            DedupeArticlesStep(),
            BuildClustersStep(),
            CollectMarketIndicesStep(),
            GenerateAiSummariesStep(),
            BuildPageSnapshotStep(),
            FinalizeJobStep(),
        ]

    async def run(self, job_id: int) -> None:
        async with self._session_maker() as session:
            repository = BatchJobRepository(session)
            context: BatchExecutionContext | None = None
            last_committed_context: BatchExecutionContext | None = None
            try:
                job = await repository.get_job_by_id(job_id)
                if job is None:
                    raise RuntimeError(f'Batch job {job_id} was not found.')
                context = BatchExecutionContext(
                    job_id=job.job_id,
                    business_date=job.business_date,
                    force_run=bool(job.force_run),
                    rebuild_page_only=bool(job.rebuild_page_only),
                )
                await repository.add_event(
                    job_id=job_id,
                    step_code='ORCHESTRATE',
                    level=EventLevel.INFO.value,
                    message='Market daily batch orchestrator started.',
                )
                await repository.commit()
                last_committed_context = deepcopy(context)
                for step in self._steps:
                    context = await step.execute(repository, context)
                    await repository.commit()
                    last_committed_context = deepcopy(context)
            except Exception as exc:
                await _rollback_active_transaction(repository)
                error_code = (
                    exc.error_code
                    if isinstance(exc, BatchPipelineError)
                    else 'INTERNAL_BATCH_ERROR'
                )
                error_message = (
                    exc.error_message
                    if isinstance(exc, BatchPipelineError)
                    else '배치 오케스트레이터 실행 중 오류가 발생했습니다.'
                )
                await repository.add_event(
                    job_id=job_id,
                    step_code='ORCHESTRATE',
                    level=EventLevel.ERROR.value,
                    message='Market daily batch orchestrator failed.',
                    context_json={
                        'errorCode': error_code,
                        'error': {
                            'errorClass': type(exc).__name__,
                            'errorMessage': str(exc),
                        },
                    },
                )
                await repository.commit()
                failure_context = last_committed_context
                if failure_context is not None and _has_progress_or_diagnostics(
                    failure_context
                ):
                    partial_message = failure_context.partial_message
                    if not partial_message:
                        diagnostics = list(
                            dict.fromkeys(
                                [
                                    *failure_context.partial_reasons,
                                    *failure_context.warning_messages,
                                ]
                            )
                        )
                        if diagnostics:
                            partial_message = '; '.join(diagnostics[:3])
                        elif failure_context.fallback_count:
                            partial_message = (
                                'Fallback processing was used '
                                f'{failure_context.fallback_count} time(s).'
                            )
                    await repository.mark_job_completed(
                        job_id=job_id,
                        status='FAILED',
                        raw_news_count=failure_context.raw_news_count,
                        processed_news_count=failure_context.processed_news_count,
                        cluster_count=failure_context.cluster_count,
                        page_id=failure_context.page_id,
                        page_version_no=failure_context.page_version_no,
                        partial_message=partial_message,
                        error_code=error_code,
                        error_message=error_message,
                        log_summary=' '.join(failure_context.log_messages) or None,
                    )
                else:
                    await repository.mark_job_failed(
                        job_id=job_id,
                        error_code=error_code,
                        error_message=error_message,
                    )
                await repository.commit()
                raise


def _has_progress_or_diagnostics(context: BatchExecutionContext) -> bool:
    return bool(
        context.raw_news_count
        or context.processed_news_count
        or context.cluster_count
        or context.page_id is not None
        or context.page_version_no is not None
        or context.partial_message
        or context.partial_reasons
        or context.warning_messages
        or context.fallback_count
    )


async def _rollback_active_transaction(repository: BatchJobRepository) -> None:
    session = repository.session
    in_transaction = getattr(session, 'in_transaction', None)
    if callable(in_transaction):
        if in_transaction():
            await repository.rollback()
        return
    pending_domain_writes = getattr(session, 'pending_domain_writes', None)
    if pending_domain_writes:
        await repository.rollback()


__all__ = ['MarketDailyBatchOrchestrator']
