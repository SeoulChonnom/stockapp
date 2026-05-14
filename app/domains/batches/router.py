from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import (  # pyright: ignore[reportMissingImports]
    APIRouter,
    BackgroundTasks,
    Depends,
    Path,
    Query,
)

from app.api.deps import AdminDep, DbSession
from app.core.response import ApiSuccess
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.domains.batches.assembler import (
    assemble_batch_job_detail_response,
    assemble_batch_job_list_response,
    assemble_batch_run_response,
)
from app.domains.batches.service import (
    BatchesService,
    BatchJobScheduler,
)
from app.schemas.batch import (
    BatchJobDetailResponse,
    BatchJobListResponse,
    BatchRunRequest,
    BatchRunResponse,
)

router = APIRouter()


def get_batches_service(session: DbSession) -> BatchesService:
    return BatchesService(BatchJobRepository(session))


def get_batch_job_scheduler() -> BatchJobScheduler:
    return BatchJobScheduler()


BatchesServiceDep = Annotated[BatchesService, Depends(get_batches_service)]
BatchSchedulerDep = Annotated[BatchJobScheduler, Depends(get_batch_job_scheduler)]


@router.post('/market-daily', response_model=ApiSuccess[BatchRunResponse])
async def start_market_daily_batch(
    payload: BatchRunRequest,
    background_tasks: BackgroundTasks,
    current_user: AdminDep,
    service: BatchesServiceDep,
    scheduler: BatchSchedulerDep,
) -> ApiSuccess[BatchRunResponse]:
    result = await service.start_market_daily_batch(
        business_date=payload.businessDate,
        user_id=current_user.user_id,
        force=payload.force,
        rebuild_page_only=payload.rebuildPageOnly,
    )
    background_tasks.add_task(scheduler.run_market_daily, result['jobId'])
    return ApiSuccess(data=assemble_batch_run_response(result))


@router.get('/jobs', response_model=ApiSuccess[BatchJobListResponse])
async def list_batch_jobs(
    _: AdminDep,
    service: BatchesServiceDep,
    fromDate: Annotated[date | None, Query(alias='fromDate')] = None,
    toDate: Annotated[date | None, Query(alias='toDate')] = None,
    status: Annotated[str | None, Query(alias='status')] = None,
    page: Annotated[int, Query(alias='page', ge=1)] = 1,
    size: Annotated[int, Query(alias='size', ge=1, le=100)] = 20,
) -> ApiSuccess[BatchJobListResponse]:
    result = await service.list_jobs(
        from_date=fromDate,
        to_date=toDate,
        status=status,
        page=page,
        size=size,
    )
    return ApiSuccess(data=assemble_batch_job_list_response(result))


@router.get('/jobs/{jobId}', response_model=ApiSuccess[BatchJobDetailResponse])
async def get_batch_job_detail(
    _: AdminDep,
    service: BatchesServiceDep,
    jobId: Annotated[int, Path(alias='jobId', ge=1)],
) -> ApiSuccess[BatchJobDetailResponse]:
    result = await service.get_job_detail(jobId)
    return ApiSuccess(data=assemble_batch_job_detail_response(result))


__all__ = ['get_batch_job_scheduler', 'get_batches_service', 'router']
