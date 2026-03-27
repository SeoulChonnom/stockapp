from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


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
    partial_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_messages: list[str] = field(default_factory=list)


__all__ = ["BatchExecutionContext"]
