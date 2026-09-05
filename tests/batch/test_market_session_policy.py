from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.batch.policies.market_session_policy import MarketSessionPolicy
from app.core.settings import Settings

KST = ZoneInfo('Asia/Seoul')


def _policy(*, grace_minutes: int = 30) -> MarketSessionPolicy:
    return MarketSessionPolicy(
        settings=Settings(market_session_data_grace_minutes=grace_minutes)
    )


@pytest.mark.parametrize(
    ('market_type', 'as_of', 'expected_session_date'),
    [
        (
            'US',
            datetime(2026, 7, 28, 0, 4, tzinfo=KST),
            date(2026, 7, 24),
        ),
        (
            'KR',
            datetime(2026, 7, 28, 0, 4, tzinfo=KST),
            date(2026, 7, 27),
        ),
        (
            'US',
            datetime(2026, 7, 28, 7, 0, tzinfo=KST),
            date(2026, 7, 27),
        ),
        (
            'KR',
            datetime(2026, 7, 28, 7, 0, tzinfo=KST),
            date(2026, 7, 27),
        ),
    ],
)
def test_latest_completed_session_uses_exchange_close_and_data_grace(
    market_type,
    as_of,
    expected_session_date,
):
    session = _policy().latest_completed_session(
        market_type=market_type,
        as_of=as_of,
    )

    assert session.expected_session_date == expected_session_date


@pytest.mark.parametrize(
    ('market_type', 'as_of', 'expected_session_date'),
    [
        (
            'US',
            datetime(2026, 7, 5, 12, 0, tzinfo=KST),
            date(2026, 7, 2),
        ),
        (
            'KR',
            datetime(2026, 8, 16, 12, 0, tzinfo=KST),
            date(2026, 8, 14),
        ),
        (
            'US',
            datetime(2026, 1, 2, 12, 0, tzinfo=KST),
            date(2025, 12, 31),
        ),
        (
            'KR',
            datetime(2026, 1, 2, 12, 0, tzinfo=KST),
            date(2025, 12, 30),
        ),
    ],
)
def test_latest_completed_session_skips_weekends_and_exchange_holidays(
    market_type,
    as_of,
    expected_session_date,
):
    session = _policy().latest_completed_session(
        market_type=market_type,
        as_of=as_of,
    )

    assert session.expected_session_date == expected_session_date


def test_us_session_close_reflects_daylight_saving_time():
    policy = _policy(grace_minutes=0)

    winter = policy.latest_completed_session(
        market_type='US',
        as_of=datetime(2026, 1, 6, 12, 0, tzinfo=KST),
    )
    summer = policy.latest_completed_session(
        market_type='US',
        as_of=datetime(2026, 7, 7, 12, 0, tzinfo=KST),
    )

    assert winter.session_close_at.hour == 21
    assert summer.session_close_at.hour == 20


def test_build_context_uses_first_run_24_hour_window():
    as_of = datetime(2026, 7, 28, 7, 0, tzinfo=KST)

    draft = _policy().build_context(
        market_type='US',
        as_of=as_of,
        previous_coverage_end_at=None,
    )

    assert draft.news_window_end_at == as_of.astimezone(UTC)
    assert draft.news_window_start_at == as_of.astimezone(UTC) - timedelta(hours=24)


def test_build_context_continues_from_last_complete_coverage_watermark():
    as_of = datetime(2026, 7, 28, 7, 0, tzinfo=KST)
    previous_end = datetime(2026, 7, 27, 21, 30, tzinfo=UTC)

    draft = _policy().build_context(
        market_type='KR',
        as_of=as_of,
        previous_coverage_end_at=previous_end,
    )

    assert draft.news_window_start_at == previous_end
    assert draft.news_window_end_at == as_of.astimezone(UTC)


def test_build_context_caps_a_watermark_that_can_no_longer_be_collected():
    """A frozen watermark must not grow the window without bound.

    One collection slot that never ran leaves coverage permanently incomplete,
    so the watermark stops advancing and every later window carries the same
    hole. This is the escape: a slot that old can no longer be collected, so
    the window moves forward instead of chasing it forever.
    """
    as_of = datetime(2026, 9, 6, 6, 10, tzinfo=KST)
    # The watermark production actually froze at, eleven days back.
    frozen_watermark = datetime(2026, 8, 26, 6, 10, tzinfo=KST)

    draft = _policy().build_context(
        market_type='KR',
        as_of=as_of,
        previous_coverage_end_at=frozen_watermark,
    )

    assert draft.news_window_start_at == as_of.astimezone(UTC) - timedelta(hours=48)
    assert draft.news_window_end_at - draft.news_window_start_at == timedelta(hours=48)
    assert draft.lookback_capped is True


def test_build_context_leaves_a_healthy_daily_watermark_alone():
    """The cap is a backstop, so it must never fire in normal operation.

    Daily runs drift either side of exactly 24 hours, so a cap set at the run
    interval would win on half the days and start the window a fraction of a
    second after the previous one ended -- dropping whatever landed in between.
    """
    as_of = datetime(2026, 9, 6, 6, 10, 2, tzinfo=KST)
    yesterday = datetime(2026, 9, 5, 6, 10, 1, tzinfo=KST)

    draft = _policy().build_context(
        market_type='KR',
        as_of=as_of,
        previous_coverage_end_at=yesterday,
    )

    assert draft.news_window_start_at == yesterday.astimezone(UTC)
    assert draft.lookback_capped is False


def test_build_context_still_catches_up_after_one_missed_daily_run():
    """48 hours is chosen so a skipped daily batch loses no news."""
    as_of = datetime(2026, 9, 6, 6, 10, tzinfo=KST)
    two_days_back = datetime(2026, 9, 4, 6, 10, tzinfo=KST)

    draft = _policy().build_context(
        market_type='KR',
        as_of=as_of,
        previous_coverage_end_at=two_days_back,
    )

    assert draft.news_window_start_at == two_days_back.astimezone(UTC)
    assert draft.lookback_capped is False


def test_build_context_does_not_cap_a_first_run():
    draft = _policy().build_context(
        market_type='US',
        as_of=datetime(2026, 9, 6, 6, 10, tzinfo=KST),
        previous_coverage_end_at=None,
    )

    assert draft.lookback_capped is False


def test_policy_rejects_naive_cutoff():
    with pytest.raises(ValueError, match='timezone-aware'):
        _policy().latest_completed_session(
            market_type='US',
            as_of=datetime(2026, 7, 28, 7, 0),
        )
