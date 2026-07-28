from __future__ import annotations

from dataclasses import dataclass

from app.batch.ai_summary_targets import AiSummaryTarget
from app.db.repositories.projections import AiSummaryRecord


@dataclass(slots=True)
class AiRetryCounts:
    target_count: int = 0
    attempted_count: int = 0
    success_count: int = 0
    fallback_count: int = 0
    failed_count: int = 0
    recovered_count: int = 0


@dataclass(frozen=True, slots=True)
class AiRetrySelection:
    target: AiSummaryTarget
    source_summary: AiSummaryRecord
    existing_retry: AiSummaryRecord | None = None


@dataclass(frozen=True, slots=True)
class AiRetryPageResult:
    page_id: int
    version_no: int
    status: str
    partial_message: str | None


@dataclass(frozen=True, slots=True)
class AiRetryRunResult:
    counts: AiRetryCounts
    page: AiRetryPageResult | None
    status: str
    partial_message: str | None


__all__ = [
    'AiRetryCounts',
    'AiRetryPageResult',
    'AiRetryRunResult',
    'AiRetrySelection',
]
