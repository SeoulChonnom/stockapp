from __future__ import annotations

from datetime import datetime, timedelta

from app.core.timezone import KST

NEWS_COLLECTION_SLOT_MINUTES = 30


def resolve_completed_news_slot(now: datetime) -> tuple[datetime, datetime]:
    """Return the newest news collection slot that has already finished.

    News is collected in fixed KST-aligned slots that are only recorded once
    they end, so nothing after this slot's end can have been collected yet.
    Both the enqueuer and the coverage check read that boundary from here, so
    they cannot disagree about what "already collectable" means.
    """
    if now.tzinfo is None:
        raise ValueError('News collection clock must be timezone-aware.')
    local_now = now.astimezone(KST)
    aligned_minute = (
        local_now.minute // NEWS_COLLECTION_SLOT_MINUTES
    ) * NEWS_COLLECTION_SLOT_MINUTES
    window_end_at = local_now.replace(
        minute=aligned_minute,
        second=0,
        microsecond=0,
    )
    window_start_at = window_end_at - timedelta(minutes=NEWS_COLLECTION_SLOT_MINUTES)
    return window_start_at, window_end_at


def collectable_window_end(window_end_at: datetime, *, now: datetime) -> datetime:
    """Clamp a coverage window to what completed slots could possibly cover.

    A market context's news window ends at the wall-clock instant the batch
    started, which lands mid-slot. Requiring coverage up to that instant makes
    every run look incomplete, because the slot containing it has not ended.
    """
    _, latest_completed_end_at = resolve_completed_news_slot(now)
    return min(window_end_at, latest_completed_end_at)


__all__ = [
    'NEWS_COLLECTION_SLOT_MINUTES',
    'collectable_window_end',
    'resolve_completed_news_slot',
]
