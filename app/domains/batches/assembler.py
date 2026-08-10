from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.core.error_diagnostics import mask_error_log
from app.core.public_diagnostics import sanitize_public_diagnostic
from app.db.enums import BatchJobType, derive_batch_job_type
from app.schemas.batch import (
    AiRetryRunResponse,
    BatchJobDetailResponse,
    BatchJobListItemResponse,
    BatchJobListResponse,
    BatchJobNewsCollectionDetail,
    BatchJobSnapshotDetail,
    BatchJobsPaginationResponse,
    BatchJobStepRunResponse,
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
    steps = [
        {
            **step,
            'errorMessage': sanitize_public_diagnostic(step.get('errorMessage')),
            'errorLog': mask_error_log(step.get('errorLog')),
        }
        for step in payload.get('steps', [])
    ]
    return BatchJobDetailResponse.model_validate(
        {
            **payload,
            'partialMessage': sanitize_public_diagnostic(payload.get('partialMessage')),
            'errorMessage': sanitize_public_diagnostic(payload.get('errorMessage')),
            'logSummary': sanitize_public_diagnostic(payload.get('logSummary')),
            'steps': steps,
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


def build_ai_retry_run_payload(job: Any, *, created: bool) -> dict[str, Any]:
    return {
        'jobId': job.job_id,
        'jobName': job.job_name,
        'businessDate': job.business_date.isoformat(),
        'status': job.status,
        'runMode': job.run_mode,
        'sourceJobId': job.source_job_id,
        'sourcePageId': job.source_page_id,
        'idempotencyKey': job.idempotency_key,
        'startedAt': job.started_at.isoformat(),
        '_created': created,
    }


def build_batch_job_list_payload(result: Any) -> dict[str, Any]:
    items = [
        BatchJobListItemResponse(
            jobId=item.job_id,
            jobType=derive_batch_job_type(item.run_mode).value,
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


def build_batch_job_detail_payload(
    job: Any,
    news_run: Any | None = None,
    step_runs: Sequence[Any] | None = None,
) -> dict[str, Any]:
    job_type = derive_batch_job_type(job.run_mode)
    snapshot: BatchJobSnapshotDetail | None = None
    news_collection: BatchJobNewsCollectionDetail | None = None
    if job_type == BatchJobType.MARKET_SNAPSHOT:
        snapshot = BatchJobSnapshotDetail(
            forceRun=job.force_run,
            rebuildPageOnly=job.rebuild_page_only,
            rawNewsCount=job.raw_news_count,
            processedNewsCount=job.processed_news_count,
            clusterCount=job.cluster_count,
            pageId=job.page_id,
            pageVersionNo=job.page_version_no,
            aiTargetCount=job.ai_target_count,
            aiAttemptedCount=job.ai_attempted_count,
            aiSuccessCount=job.ai_success_count,
            aiFallbackCount=job.ai_fallback_count,
            aiFailedCount=job.ai_failed_count,
            aiRecoveredCount=job.ai_recovered_count,
        )
    elif job_type == BatchJobType.NEWS_COLLECTION and news_run is not None:
        news_collection = BatchJobNewsCollectionDetail(
            runId=news_run.run_id,
            providerName=news_run.provider_name,
            windowStartAt=_as_required_iso(news_run.window_start_at),
            windowEndAt=_as_required_iso(news_run.window_end_at),
            queryStartAt=_as_required_iso(news_run.query_start_at),
            queryEndAt=_as_required_iso(news_run.query_end_at),
            totalKeywordCount=news_run.total_keyword_count,
            completedKeywordCount=news_run.completed_keyword_count,
            fetchedCount=news_run.fetched_count,
            matchedCount=news_run.matched_count,
            insertedCount=news_run.inserted_count,
            coverageComplete=news_run.coverage_complete,
        )

    payload = BatchJobDetailResponse(
        jobId=job.job_id,
        jobName=job.job_name,
        jobType=job_type.value,
        businessDate=job.business_date,
        status=job.status,
        runMode=job.run_mode,
        sourceJobId=job.source_job_id,
        sourcePageId=job.source_page_id,
        queuedAt=_as_iso(job.queued_at),
        attemptCount=job.attempt_count,
        maxAttempts=job.max_attempts,
        currentStep=job.current_step,
        startedAt=_as_required_iso(job.started_at),
        endedAt=_as_iso(job.ended_at),
        durationSeconds=job.duration_seconds,
        partialMessage=sanitize_public_diagnostic(job.partial_message),
        errorCode=job.error_code,
        errorMessage=sanitize_public_diagnostic(job.error_message),
        logSummary=sanitize_public_diagnostic(job.log_summary),
        snapshot=snapshot,
        newsCollection=news_collection,
        steps=[
            BatchJobStepRunResponse(
                stepCode=step_run.step_code,
                status=step_run.status,
                startedAt=_as_required_iso(step_run.started_at),
                endedAt=_as_iso(step_run.ended_at),
                durationMs=step_run.duration_ms,
                errorMessage=sanitize_public_diagnostic(step_run.error_message),
                errorLog=mask_error_log(step_run.error_log),
            )
            for step_run in (step_runs or [])
        ],
    )
    return payload.model_dump(mode='json')


__all__ = [
    'assemble_ai_retry_run_response',
    'assemble_batch_job_detail_response',
    'assemble_batch_job_list_response',
    'assemble_batch_run_response',
    'build_ai_retry_run_payload',
    'build_batch_job_detail_payload',
    'build_batch_job_list_payload',
    'build_batch_run_payload',
]
