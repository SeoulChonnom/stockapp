from __future__ import annotations

from datetime import UTC, date, datetime

from app.db.repositories.projections import BatchJobMarketContextRecord


class CompleteMarketContextRepository:
    """Provide deterministic US/KR contexts to batch step unit tests."""

    def __init__(self, _session) -> None:
        pass

    async def list_for_job(self, job_id: int) -> list[BatchJobMarketContextRecord]:
        return [
            BatchJobMarketContextRecord(
                market_context_id=index,
                batch_job_id=job_id,
                market_type=market_type,
                expected_session_date=date(2026, 3, 17),
                actual_index_source_date=date(2026, 3, 17),
                session_close_at=datetime(2026, 3, 17, 20, 0, tzinfo=UTC),
                news_window_start_at=datetime(2026, 3, 16, 22, 0, tzinfo=UTC),
                news_window_end_at=datetime(2026, 3, 17, 22, 0, tzinfo=UTC),
                news_coverage_complete=True,
            )
            for index, market_type in enumerate(('US', 'KR'), start=1)
        ]


__all__ = ['CompleteMarketContextRepository']
