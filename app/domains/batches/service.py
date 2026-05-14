from __future__ import annotations

from datetime import date

from fastapi import BackgroundTasks

from app.api.deps import DbSession
from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator
from app.core.exceptions import ConflictError, NotFoundError
from app.core.timezone import get_business_date
from app.db.enums import BatchJobStatus, BatchTriggerType
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.projections import BatchJobCreateParams
from app.schemas.batch import (
    BatchJobDetailResponse,
    BatchJobListItemResponse,
    BatchJobListResponse,
    BatchJobsPaginationResponse,
    BatchJobSummaryResponse,
    BatchRunResponse,
)


class BatchJobScheduler:
    def __init__(
        self, orchestrator: MarketDailyBatchOrchestrator | None = None
    ) -> None:
        self._orchestrator = orchestrator or MarketDailyBatchOrchestrator()

    def schedule(self, background_tasks: BackgroundTasks, job_id: int) -> None:
        background_tasks.add_task(self._orchestrator.run, job_id)


class BatchesService:
    def __init__(self, repository: BatchJobRepository) -> None:
        self._repo = repository

    async def list_jobs(
        self,
        *,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
        page: int,
        size: int,
    ) -> BatchJobListResponse:
        result = await self._repo.list_jobs(
            from_date=from_date,
            to_date=to_date,
            status=status,
            page=page,
            size=size,
        )
        return BatchJobListResponse(
            items=[
                BatchJobListItemResponse(
                    jobId=item.job_id,
                    jobName=item.job_name,
                    businessDate=item.business_date,
                    status=item.status,
                    startedAt=item.started_at,
                    endedAt=item.ended_at,
                    durationSeconds=item.duration_seconds,
                    marketScope=item.market_scope,
                    rawNewsCount=item.raw_news_count,
                    processedNewsCount=item.processed_news_count,
                    clusterCount=item.cluster_count,
                    pageId=item.page_id,
                    pageVersionNo=item.page_version_no,
                    partialMessage=item.partial_message,
                )
                for item in result.items
            ],
            pagination=BatchJobsPaginationResponse(
                page=result.page,
                size=result.size,
                totalCount=result.total_count,
            ),
            summary=BatchJobSummaryResponse(
                successCount=result.summary.success_count,
                partialCount=result.summary.partial_count,
                failedCount=result.summary.failed_count,
                avgDurationSeconds=result.summary.avg_duration_seconds,
            ),
        )

    async def get_job_detail(self, job_id: int) -> BatchJobDetailResponse:
        job = await self._repo.get_job_by_id(job_id)
        if job is None:
            raise NotFoundError(
                'BATCH_JOB_NOT_FOUND', '요청한 배치 작업을 찾을 수 없습니다.'
            )
        return BatchJobDetailResponse(
            jobId=job.job_id,
            jobName=job.job_name,
            businessDate=job.business_date,
            status=job.status,
            forceRun=job.force_run,
            rebuildPageOnly=job.rebuild_page_only,
            startedAt=job.started_at,
            endedAt=job.ended_at,
            durationSeconds=job.duration_seconds,
            rawNewsCount=job.raw_news_count,
            processedNewsCount=job.processed_news_count,
            clusterCount=job.cluster_count,
            pageId=job.page_id,
            pageVersionNo=job.page_version_no,
            partialMessage=job.partial_message,
            errorCode=job.error_code,
            errorMessage=job.error_message,
            logSummary=job.log_summary,
        )

    async def start_market_daily_batch(
        self,
        *,
        business_date: date | None,
        user_id: str | None,
        force: bool,
        rebuild_page_only: bool,
    ) -> BatchRunResponse:
        resolved_business_date = business_date or get_business_date()
        if await self._repo.has_active_job_for_business_date(resolved_business_date):
            raise ConflictError(
                'BATCH_ALREADY_RUNNING',
                '동일 날짜의 배치가 이미 실행 중입니다.',
            )
        if not force and await self._repo.has_completed_page_for_business_date(
            resolved_business_date
        ):
            raise ConflictError(
                'PAGE_ALREADY_EXISTS',
                '이미 생성된 페이지가 있어 배치를 시작할 수 없습니다.',
            )

        job = await self._repo.create_job(
            BatchJobCreateParams(
                business_date=resolved_business_date,
                status=BatchJobStatus.RUNNING.value,
                trigger_type=BatchTriggerType.MANUAL.value,
                triggered_by_user_id=user_id,
                force_run=force,
                rebuild_page_only=rebuild_page_only,
            )
        )
        await self._repo.add_event(
            job_id=job.job_id,
            step_code='CREATE_JOB',
            level='INFO',
            message='Manual market daily batch requested.',
            context_json={
                'force': force,
                'rebuildPageOnly': rebuild_page_only,
            },
        )
        return BatchRunResponse(
            jobId=job.job_id,
            jobName=job.job_name,
            businessDate=job.business_date,
            status=job.status,
            startedAt=job.started_at,
        )


def get_batches_service(session: DbSession) -> BatchesService:
    return BatchesService(BatchJobRepository(session))


def get_batch_job_scheduler() -> BatchJobScheduler:
    return BatchJobScheduler()


__all__ = [
    'BatchJobScheduler',
    'BatchesService',
    'get_batch_job_scheduler',
    'get_batches_service',
]
