from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.db.enums import BatchJobStatus


def determine_batch_status(context: BatchExecutionContext) -> str:
    if context.error_code or context.error_message:
        return BatchJobStatus.FAILED.value
    if context.page_id is None:
        return BatchJobStatus.FAILED.value
    if context.partial_message or context.partial_reasons or context.warning_messages or context.fallback_count:
        return BatchJobStatus.PARTIAL.value
    return BatchJobStatus.SUCCESS.value


__all__ = ["determine_batch_status"]
