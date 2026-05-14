from __future__ import annotations

from datetime import date

from app.batch.orchestrators.market_daily import MarketDailyBatchOrchestrator
from app.core.exceptions import ConflictError, NotFoundError
from app.core.timezone import get_business_date
from app.db.enums import BatchJobStatus, BatchTriggerType
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.projections import BatchJobCreateParams
from app.domains.batches.assembler import (
    build_batch_job_detail_payload,
    build_batch_job_list_payload,
    build_batch_run_payload,
)


class BatchJobScheduler:
    def __init__(
        self, orchestrator: MarketDailyBatchOrchestrator | None = None
    ) -> None:
        self._orchestrator = orchestrator or MarketDailyBatchOrchestrator()

    async def run_market_daily(self, job_id: int) -> None:
        await self._orchestrator.run(job_id)


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
    ) -> dict[str, object]:
        result = await self._repo.list_jobs(
            from_date=from_date,
            to_date=to_date,
            status=status,
            page=page,
            size=size,
        )
        return build_batch_job_list_payload(result)

    async def get_job_detail(self, job_id: int) -> dict[str, object]:
        job = await self._repo.get_job_by_id(job_id)
        if job is None:
            raise NotFoundError(
                'BATCH_JOB_NOT_FOUND', '요청한 배치 작업을 찾을 수 없습니다.'
            )
        return build_batch_job_detail_payload(job)

    async def start_market_daily_batch(
        self,
        *,
        business_date: date | None,
        user_id: str | None,
        force: bool,
        rebuild_page_only: bool,
    ) -> dict[str, object]:
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
        await self._repo.commit()
        return build_batch_run_payload(job)


__all__ = [
    'BatchJobScheduler',
    'BatchesService',
]
