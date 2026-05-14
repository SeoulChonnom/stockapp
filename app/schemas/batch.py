from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class BatchRunRequest(BaseModel):
    businessDate: date | None = None
    force: bool = False
    rebuildPageOnly: bool = False


class BatchRunResponse(BaseModel):
    jobId: int
    jobName: str
    businessDate: date
    status: str
    startedAt: datetime | str


class BatchJobListItemResponse(BaseModel):
    jobId: int
    jobName: str
    businessDate: date
    status: str
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationSeconds: int | None = None
    marketScope: str
    rawNewsCount: int
    processedNewsCount: int
    clusterCount: int
    pageId: int | None = None
    pageVersionNo: int | None = None
    partialMessage: str | None = None


class BatchJobsPaginationResponse(BaseModel):
    page: int
    size: int
    totalCount: int


class BatchJobSummaryResponse(BaseModel):
    successCount: int
    partialCount: int
    failedCount: int
    avgDurationSeconds: int


class BatchJobListResponse(BaseModel):
    items: list[BatchJobListItemResponse]
    pagination: BatchJobsPaginationResponse
    summary: BatchJobSummaryResponse


class BatchJobDetailResponse(BaseModel):
    jobId: int
    jobName: str
    businessDate: date
    status: str
    forceRun: bool | None = None
    rebuildPageOnly: bool | None = None
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationSeconds: int | None = None
    rawNewsCount: int
    processedNewsCount: int
    clusterCount: int
    pageId: int | None = None
    pageVersionNo: int | None = None
    partialMessage: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    logSummary: str | None = None


__all__ = [
    'BatchJobDetailResponse',
    'BatchJobListItemResponse',
    'BatchJobListResponse',
    'BatchJobSummaryResponse',
    'BatchJobsPaginationResponse',
    'BatchRunRequest',
    'BatchRunResponse',
]
