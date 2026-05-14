from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.batch import (
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


def assemble_batch_run_response(payload: dict[str, Any]) -> BatchRunResponse:
    return BatchRunResponse.model_validate(payload)


def assemble_batch_job_list_response(payload: dict[str, Any]) -> BatchJobListResponse:
    return BatchJobListResponse.model_validate(payload)


def assemble_batch_job_detail_response(
    payload: dict[str, Any]
) -> BatchJobDetailResponse:
    return BatchJobDetailResponse.model_validate(payload)


def build_batch_run_payload(job: Any) -> dict[str, Any]:
    return {
        'jobId': job.job_id,
        'jobName': job.job_name,
        'businessDate': job.business_date.isoformat(),
        'status': job.status,
        'startedAt': _as_iso(job.started_at),
    }


def build_batch_job_list_payload(result: Any) -> dict[str, Any]:
    items = [
        BatchJobListItemResponse(
            jobId=item.job_id,
            jobName=item.job_name,
            businessDate=item.business_date,
            status=item.status,
            startedAt=_as_iso(item.started_at),
            endedAt=_as_iso(item.ended_at),
            durationSeconds=item.duration_seconds,
            marketScope=item.market_scope,
            rawNewsCount=item.raw_news_count,
            processedNewsCount=item.processed_news_count,
            clusterCount=item.cluster_count,
            pageId=item.page_id,
            pageVersionNo=item.page_version_no,
            partialMessage=item.partial_message,
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
        forceRun=job.force_run,
        rebuildPageOnly=job.rebuild_page_only,
        startedAt=_as_iso(job.started_at),
        endedAt=_as_iso(job.ended_at),
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
    return payload.model_dump(mode='json')


__all__ = [
    'assemble_batch_job_detail_response',
    'assemble_batch_job_list_response',
    'assemble_batch_run_response',
    'build_batch_job_detail_payload',
    'build_batch_job_list_payload',
    'build_batch_run_payload',
]
