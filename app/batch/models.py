from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(slots=True)
class BatchExecutionContext:
    job_id: int
    business_date: date
    force_run: bool
    rebuild_page_only: bool
    raw_news_count: int = 0
    processed_news_count: int = 0
    cluster_count: int = 0
    page_id: int | None = None
    page_version_no: int | None = None
    collected_index_count: int = 0
    generated_summary_count: int = 0
    fallback_count: int = 0
    partial_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    partial_reasons: list[str] = field(default_factory=list)
    warning_messages: list[str] = field(default_factory=list)
    log_messages: list[str] = field(default_factory=list)

    def to_checkpoint(self) -> dict[str, Any]:
        """Serialize resumable batch progress to a JSON-compatible mapping."""
        return {
            'jobId': self.job_id,
            'businessDate': self.business_date.isoformat(),
            'forceRun': self.force_run,
            'rebuildPageOnly': self.rebuild_page_only,
            'rawNewsCount': self.raw_news_count,
            'processedNewsCount': self.processed_news_count,
            'clusterCount': self.cluster_count,
            'pageId': self.page_id,
            'pageVersionNo': self.page_version_no,
            'collectedIndexCount': self.collected_index_count,
            'generatedSummaryCount': self.generated_summary_count,
            'fallbackCount': self.fallback_count,
            'partialMessage': self.partial_message,
            'errorCode': self.error_code,
            'errorMessage': self.error_message,
            'partialReasons': self.partial_reasons,
            'warningMessages': self.warning_messages,
            'logMessages': self.log_messages,
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
    ) -> BatchExecutionContext:
        """Restore a context while treating malformed checkpoint data as empty."""
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            job_id=job_id,
            business_date=business_date,
            force_run=force_run,
            rebuild_page_only=rebuild_page_only,
            raw_news_count=_checkpoint_int(payload, 'rawNewsCount'),
            processed_news_count=_checkpoint_int(payload, 'processedNewsCount'),
            cluster_count=_checkpoint_int(payload, 'clusterCount'),
            page_id=_checkpoint_optional_int(payload, 'pageId'),
            page_version_no=_checkpoint_optional_int(payload, 'pageVersionNo'),
            collected_index_count=_checkpoint_int(payload, 'collectedIndexCount'),
            generated_summary_count=_checkpoint_int(payload, 'generatedSummaryCount'),
            fallback_count=_checkpoint_int(payload, 'fallbackCount'),
            partial_message=_checkpoint_optional_string(payload, 'partialMessage'),
            error_code=_checkpoint_optional_string(payload, 'errorCode'),
            error_message=_checkpoint_optional_string(payload, 'errorMessage'),
            partial_reasons=_checkpoint_string_list(payload, 'partialReasons'),
            warning_messages=_checkpoint_string_list(payload, 'warningMessages'),
            log_messages=_checkpoint_string_list(payload, 'logMessages'),
        )


def _checkpoint_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    return value if isinstance(value, int) and value >= 0 else 0


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
