from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.public_diagnostics import sanitize_public_diagnostic
from app.schemas.batch import (
    AiRetryRunResponse,
    BatchJobDetailResponse,
    BatchJobListItemResponse,
    BatchJobListResponse,
    BatchJobsPaginationResponse,
    BatchJobSummaryResponse,
    BatchRunResponse,
)


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_required_iso(value: Any) -> str:
    iso = _as_iso(value)
    if iso is None:
        raise ValueError('required datetime value is missing')
    return iso


def assemble_batch_run_response(payload: dict[str, Any]) -> BatchRunResponse:
    return BatchRunResponse.model_validate(payload)


def assemble_ai_retry_run_response(
    payload: dict[str, Any],
) -> AiRetryRunResponse:
    return AiRetryRunResponse.model_validate(payload)


def assemble_batch_job_list_response(payload: dict[str, Any]) -> BatchJobListResponse:
    safe_payload = dict(payload)
    safe_payload['items'] = [
        {
            **item,
            'partialMessage': sanitize_public_diagnostic(item.get('partialMessage')),
        }
        for item in payload.get('items', [])
    ]
    return BatchJobListResponse.model_validate(safe_payload)


def assemble_batch_job_detail_response(
    payload: dict[str, Any],
) -> BatchJobDetailResponse:
    return BatchJobDetailResponse.model_validate(
        {
            **payload,
            'partialMessage': sanitize_public_diagnostic(payload.get('partialMessage')),
            'errorMessage': sanitize_public_diagnostic(payload.get('errorMessage')),
            'logSummary': sanitize_public_diagnostic(payload.get('logSummary')),
        }
    )


def build_batch_run_payload(job: Any) -> dict[str, Any]:
    return {
        'jobId': job.job_id,
        'jobName': job.job_name,
        'businessDate': job.business_date.isoformat(),
        'status': job.status,
        'startedAt': _as_iso(job.started_at),
        'queuedAt': _as_iso(job.queued_at),
    }


def build_batch_job_list_payload(result: Any) -> dict[str, Any]:
    items = [
        BatchJobListItemResponse(
            jobId=item.job_id,
            jobName=item.job_name,
            businessDate=item.business_date,
            status=item.status,
            runMode=item.run_mode,
            sourceJobId=item.source_job_id,
            sourcePageId=item.source_page_id,
            queuedAt=_as_iso(item.queued_at),
            attemptCount=item.attempt_count,
            maxAttempts=item.max_attempts,
            currentStep=item.current_step,
            startedAt=_as_required_iso(item.started_at),
            endedAt=_as_iso(item.ended_at),
            durationSeconds=item.duration_seconds,
            marketScope=item.market_scope,
            rawNewsCount=item.raw_news_count,
            processedNewsCount=item.processed_news_count,
            clusterCount=item.cluster_count,
            pageId=item.page_id,
            pageVersionNo=item.page_version_no,
            partialMessage=sanitize_public_diagnostic(item.partial_message),
            aiTargetCount=item.ai_target_count,
            aiAttemptedCount=item.ai_attempted_count,
            aiSuccessCount=item.ai_success_count,
            aiFallbackCount=item.ai_fallback_count,
            aiFailedCount=item.ai_failed_count,
            aiRecoveredCount=item.ai_recovered_count,
        ).model_dump(mode='json')
        for item in result.items
    ]
    pagination = BatchJobsPaginationResponse(
        page=result.page,
        size=result.size,
        totalCount=result.total_count,
    ).model_dump(mode='json')
    summary = BatchJobSummaryResponse(
        successCount=result.summary.success_count,
        partialCount=result.summary.partial_count,
        failedCount=result.summary.failed_count,
        avgDurationSeconds=result.summary.avg_duration_seconds,
    ).model_dump(mode='json')
    return {
        'items': items,
        'pagination': pagination,
        'summary': summary,
    }


def build_batch_job_detail_payload(job: Any) -> dict[str, Any]:
    payload = BatchJobDetailResponse(
        jobId=job.job_id,
        jobName=job.job_name,
        businessDate=job.business_date,
        status=job.status,
        runMode=job.run_mode,
        sourceJobId=job.source_job_id,
        sourcePageId=job.source_page_id,
        queuedAt=_as_iso(job.queued_at),
        attemptCount=job.attempt_count,
        maxAttempts=job.max_attempts,
        currentStep=job.current_step,
        forceRun=job.force_run,
        rebuildPageOnly=job.rebuild_page_only,
        startedAt=_as_required_iso(job.started_at),
        endedAt=_as_iso(job.ended_at),
        durationSeconds=job.duration_seconds,
        rawNewsCount=job.raw_news_count,
        processedNewsCount=job.processed_news_count,
        clusterCount=job.cluster_count,
        pageId=job.page_id,
        pageVersionNo=job.page_version_no,
        partialMessage=sanitize_public_diagnostic(job.partial_message),
        errorCode=job.error_code,
        errorMessage=sanitize_public_diagnostic(job.error_message),
        logSummary=sanitize_public_diagnostic(job.log_summary),
        aiTargetCount=job.ai_target_count,
        aiAttemptedCount=job.ai_attempted_count,
        aiSuccessCount=job.ai_success_count,
        aiFallbackCount=job.ai_fallback_count,
        aiFailedCount=job.ai_failed_count,
        aiRecoveredCount=job.ai_recovered_count,
    )
    return payload.model_dump(mode='json')


__all__ = [
    'assemble_ai_retry_run_response',
    'assemble_batch_job_detail_response',
    'assemble_batch_job_list_response',
    'assemble_batch_run_response',
    'build_batch_job_detail_payload',
    'build_batch_job_list_payload',
    'build_batch_run_payload',
]
