from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker

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
                for step in self._steps:
                    context = await step.execute(repository, context)
                    await repository.commit()
            except Exception as exc:
                await _rollback_active_transaction(repository)
                await repository.add_event(
                    job_id=job_id,
                    step_code='ORCHESTRATE',
                    level=EventLevel.ERROR.value,
                    message='Market daily batch orchestrator failed.',
                    context_json={'error': str(exc)},
                )
                await repository.commit()
                await repository.mark_job_failed(
                    job_id=job_id,
                    error_code='INTERNAL_BATCH_ERROR',
                    error_message='배치 오케스트레이터 실행 중 오류가 발생했습니다.',
                )
                await repository.commit()
                raise


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
