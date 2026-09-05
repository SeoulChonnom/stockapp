from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

LLM_RETRY_COUNT_CHECKPOINT_KEY = 'llmRetryCount'


def checkpoint_llm_retry_count(checkpoint: object) -> int:
    """Read how often a job was rescheduled for a transient provider failure.

    The counter lives at the checkpoint root rather than inside the serialized
    context because the durable queue writes it while releasing the claim, and
    it must survive the reschedule that follows.
    """
    if not isinstance(checkpoint, dict):
        return 0
    value = checkpoint.get(LLM_RETRY_COUNT_CHECKPOINT_KEY)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(value, 0)


@dataclass(slots=True)
class BatchExecutionContext:
    job_id: int
    business_date: date
    force_run: bool
    rebuild_page_only: bool
    # Read fresh from the job on every attempt, never restored from the
    # serialized context, so a resumed run reports the attempt it is on.
    attempt_count: int = 1
    llm_retry_count: int = 0
    source_job_id: int | None = None
    source_page_id: int | None = None
    raw_news_count: int = 0
    processed_news_count: int = 0
    raw_news_count_by_market: dict[str, int] = field(default_factory=dict)
    processed_news_count_by_market: dict[str, int] = field(default_factory=dict)
    cluster_count: int = 0
    page_id: int | None = None
    page_version_no: int | None = None
    collected_index_count: int = 0
    generated_summary_count: int = 0
    fallback_count: int = 0
    ai_target_count: int = 0
    ai_attempted_count: int = 0
    ai_success_count: int = 0
    ai_fallback_count: int = 0
    ai_failed_count: int = 0
    # Counts CLUSTER_DETAIL_ANALYSIS rows persisted with analysisStatus !=
    # 'READY'. Kept separate from fallback_count/add_partial on purpose: those
    # feed determine_batch_status, and one cluster's degraded analysis must
    # never flip the whole daily page to PARTIAL.
    ai_detail_analysis_degraded_count: int = 0
    partial_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    partial_reasons: list[str] = field(default_factory=list)
    warning_messages: list[str] = field(default_factory=list)
    log_messages: list[str] = field(default_factory=list)
    partial_categories: dict[str, int] = field(default_factory=dict)
    # Keyed by ANALYSIS_ISSUE_MESSAGES code so build_detail_analysis_degradation_log_line
    # stays one bounded line no matter how many clusters degrade in a run.
    detail_analysis_issue_counts: dict[str, int] = field(default_factory=dict)

    def add_partial(
        self,
        category: str,
        reason: str | None = None,
        *,
        warning: str | None = None,
    ) -> None:
        """Record a PARTIAL diagnostic together with the category it belongs to.

        Every producer of a PARTIAL signal must go through here so the finalized
        log summary can report why a job degraded. A reason paired with its
        warning counts once, and re-recording an identical message is a no-op so
        checkpoint-resumed steps never inflate the counters.
        """
        recorded = False
        if reason is not None and reason not in self.partial_reasons:
            self.partial_reasons.append(reason)
            recorded = True
        if warning is not None and warning not in self.warning_messages:
            self.warning_messages.append(warning)
            recorded = True
        if recorded:
            self.partial_categories[category] = (
                self.partial_categories.get(category, 0) + 1
            )

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialize resumable batch progress to a JSON-compatible mapping."""
        return {
            'jobId': self.job_id,
            'businessDate': self.business_date.isoformat(),
            'forceRun': self.force_run,
            'rebuildPageOnly': self.rebuild_page_only,
            'sourceJobId': self.source_job_id,
            'sourcePageId': self.source_page_id,
            'rawNewsCount': self.raw_news_count,
            'processedNewsCount': self.processed_news_count,
            'rawNewsCountByMarket': self.raw_news_count_by_market,
            'processedNewsCountByMarket': self.processed_news_count_by_market,
            'clusterCount': self.cluster_count,
            'pageId': self.page_id,
            'pageVersionNo': self.page_version_no,
            'collectedIndexCount': self.collected_index_count,
            'generatedSummaryCount': self.generated_summary_count,
            'fallbackCount': self.fallback_count,
            'aiTargetCount': self.ai_target_count,
            'aiAttemptedCount': self.ai_attempted_count,
            'aiSuccessCount': self.ai_success_count,
            'aiFallbackCount': self.ai_fallback_count,
            'aiFailedCount': self.ai_failed_count,
            'aiDetailAnalysisDegradedCount': self.ai_detail_analysis_degraded_count,
            'partialMessage': self.partial_message,
            'errorCode': self.error_code,
            'errorMessage': self.error_message,
            'partialReasons': self.partial_reasons,
            'warningMessages': self.warning_messages,
            'logMessages': self.log_messages,
            'partialCategories': self.partial_categories,
            'detailAnalysisIssueCounts': self.detail_analysis_issue_counts,
        }

    @classmethod
    def from_checkpoint(
        cls,
        payload: object,
        *,
        job_id: int,
        business_date: date,
        force_run: bool,
        rebuild_page_only: bool,
        source_job_id: int | None = None,
        source_page_id: int | None = None,
        attempt_count: int = 1,
        llm_retry_count: int = 0,
    ) -> BatchExecutionContext:
        """Restore a context while treating malformed checkpoint data as empty."""
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            job_id=job_id,
            business_date=business_date,
            force_run=force_run,
            rebuild_page_only=rebuild_page_only,
            attempt_count=attempt_count,
            llm_retry_count=llm_retry_count,
            source_job_id=(
                source_job_id
                if source_job_id is not None
                else _checkpoint_optional_int(payload, 'sourceJobId')
            ),
            source_page_id=(
                source_page_id
                if source_page_id is not None
                else _checkpoint_optional_int(payload, 'sourcePageId')
            ),
            raw_news_count=_checkpoint_int(payload, 'rawNewsCount'),
            processed_news_count=_checkpoint_int(payload, 'processedNewsCount'),
            raw_news_count_by_market=_checkpoint_int_dict(
                payload, 'rawNewsCountByMarket'
            ),
            processed_news_count_by_market=_checkpoint_int_dict(
                payload, 'processedNewsCountByMarket'
            ),
            cluster_count=_checkpoint_int(payload, 'clusterCount'),
            page_id=_checkpoint_optional_int(payload, 'pageId'),
            page_version_no=_checkpoint_optional_int(payload, 'pageVersionNo'),
            collected_index_count=_checkpoint_int(payload, 'collectedIndexCount'),
            generated_summary_count=_checkpoint_int(payload, 'generatedSummaryCount'),
            fallback_count=_checkpoint_int(payload, 'fallbackCount'),
            ai_target_count=_checkpoint_int(payload, 'aiTargetCount'),
            ai_attempted_count=_checkpoint_int(payload, 'aiAttemptedCount'),
            ai_success_count=_checkpoint_int(payload, 'aiSuccessCount'),
            ai_fallback_count=_checkpoint_int(payload, 'aiFallbackCount'),
            ai_failed_count=_checkpoint_int(payload, 'aiFailedCount'),
            ai_detail_analysis_degraded_count=_checkpoint_int(
                payload, 'aiDetailAnalysisDegradedCount'
            ),
            partial_message=_checkpoint_optional_string(payload, 'partialMessage'),
            error_code=_checkpoint_optional_string(payload, 'errorCode'),
            error_message=_checkpoint_optional_string(payload, 'errorMessage'),
            partial_reasons=_checkpoint_string_list(payload, 'partialReasons'),
            warning_messages=_checkpoint_string_list(payload, 'warningMessages'),
            log_messages=_checkpoint_string_list(payload, 'logMessages'),
            partial_categories=_checkpoint_int_dict(payload, 'partialCategories'),
            detail_analysis_issue_counts=_checkpoint_int_dict(
                payload, 'detailAnalysisIssueCounts'
            ),
        )


def _checkpoint_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and value >= 0 else 0


def _checkpoint_int_dict(payload: dict[str, Any], key: str) -> dict[str, int]:
    value = payload.get(key)
    if not isinstance(value, dict):
        return {}
    return {
        market_type: count
        for market_type, count in value.items()
        if isinstance(market_type, str) and isinstance(count, int) and count >= 0
    }


def _checkpoint_optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    return value if isinstance(value, int) and value >= 0 else None


def _checkpoint_optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) else None


def _checkpoint_string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = ['BatchExecutionContext']
