from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, field_validator

from app.core.timezone import KST


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


class NewsCollectionRunRequest(BaseModel):
    slotEndAt: datetime | None = None

    @field_validator('slotEndAt')
    @classmethod
    def validate_slot_end_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError('slotEndAt must include a timezone offset.')
        local_value = value.astimezone(KST)
        if (
            local_value.minute not in {0, 30}
            or local_value.second != 0
            or local_value.microsecond != 0
        ):
            raise ValueError('slotEndAt must align to a KST 30-minute boundary.')
        return value


class NewsCollectionRunResponse(BaseModel):
    jobId: int
    runId: int
    jobName: str
    status: str
    providerName: str
    windowStartAt: datetime | str
    windowEndAt: datetime | str
    queryStartAt: datetime | str
    queryEndAt: datetime | str
    queuedAt: datetime | str


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
    jobType: str
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


class BatchJobSnapshotDetail(BaseModel):
    forceRun: bool | None = None
    rebuildPageOnly: bool | None = None
    rawNewsCount: int
    processedNewsCount: int
    clusterCount: int
    pageId: int | None = None
    pageVersionNo: int | None = None
    aiTargetCount: int = 0
    aiAttemptedCount: int = 0
    aiSuccessCount: int = 0
    aiFallbackCount: int = 0
    aiFailedCount: int = 0
    aiRecoveredCount: int = 0


class BatchJobNewsCollectionDetail(BaseModel):
    runId: int
    providerName: str
    windowStartAt: datetime | str
    windowEndAt: datetime | str
    queryStartAt: datetime | str
    queryEndAt: datetime | str
    totalKeywordCount: int
    completedKeywordCount: int
    fetchedCount: int
    matchedCount: int
    insertedCount: int
    coverageComplete: bool


class BatchJobStepRunResponse(BaseModel):
    stepCode: str
    status: str
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationMs: int | None = None


class BatchJobDetailResponse(BaseModel):
    jobId: int
    jobName: str
    jobType: str
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
    partialMessage: str | None = None
    errorCode: str | None = None
    errorMessage: str | None = None
    logSummary: str | None = None
    snapshot: BatchJobSnapshotDetail | None = None
    newsCollection: BatchJobNewsCollectionDetail | None = None
    steps: list[BatchJobStepRunResponse] = []


__all__ = [
    'AiRetryRunResponse',
    'BatchJobDetailResponse',
    'BatchJobListItemResponse',
    'BatchJobListResponse',
    'BatchJobNewsCollectionDetail',
    'BatchJobSnapshotDetail',
    'BatchJobStepRunResponse',
    'BatchJobSummaryResponse',
    'BatchJobsPaginationResponse',
    'BatchRunRequest',
    'BatchRunResponse',
    'NewsCollectionRunRequest',
    'NewsCollectionRunResponse',
]
