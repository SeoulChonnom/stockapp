from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db.repositories.news_collection_run_repo import intervals_cover_window
from app.db.repositories.projections import NewsCoverageInterval


def test_adjacent_complete_intervals_cover_requested_window():
    start = datetime(2026, 7, 31, 0, 0, tzinfo=UTC)
    intervals = [
        NewsCoverageInterval(start, start + timedelta(minutes=30)),
        NewsCoverageInterval(
            start + timedelta(minutes=30),
            start + timedelta(minutes=60),
        ),
    ]

    assert intervals_cover_window(
        intervals,
        window_start_at=start,
        window_end_at=start + timedelta(hours=1),
    )


def test_gap_between_intervals_marks_requested_window_incomplete():
    start = datetime(2026, 7, 31, 0, 0, tzinfo=UTC)
    intervals = [
        NewsCoverageInterval(start, start + timedelta(minutes=30)),
        NewsCoverageInterval(
            start + timedelta(minutes=45),
            start + timedelta(minutes=60),
        ),
    ]

    assert not intervals_cover_window(
        intervals,
        window_start_at=start,
        window_end_at=start + timedelta(hours=1),
    )
