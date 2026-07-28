from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


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
    queuedAt: datetime | str | None = None


class AiRetryRunResponse(BaseModel):
    jobId: int
    jobName: str
    businessDate: date
    status: str
    runMode: str
    sourceJobId: int
    sourcePageId: int | None = None
    idempotencyKey: str | None = None
    startedAt: datetime | str


class BatchJobListItemResponse(BaseModel):
    jobId: int
    jobName: str
    businessDate: date
    status: str
    runMode: str
    sourceJobId: int | None = None
    sourcePageId: int | None = None
    queuedAt: datetime | str | None = None
    attemptCount: int
    maxAttempts: int
    currentStep: str | None = None
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
    aiTargetCount: int = 0
    aiAttemptedCount: int = 0
    aiSuccessCount: int = 0
    aiFallbackCount: int = 0
    aiFailedCount: int = 0
    aiRecoveredCount: int = 0


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
    runMode: str
    sourceJobId: int | None = None
    sourcePageId: int | None = None
    queuedAt: datetime | str | None = None
    attemptCount: int
    maxAttempts: int
    currentStep: str | None = None
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
    aiTargetCount: int = 0
    aiAttemptedCount: int = 0
    aiSuccessCount: int = 0
    aiFallbackCount: int = 0
    aiFailedCount: int = 0
    aiRecoveredCount: int = 0


__all__ = [
    'AiRetryRunResponse',
    'BatchJobDetailResponse',
    'BatchJobListItemResponse',
    'BatchJobListResponse',
    'BatchJobSummaryResponse',
    'BatchJobsPaginationResponse',
    'BatchRunRequest',
    'BatchRunResponse',
]
