from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.batch.calendars import ExchangeCalendar, ExchangeCalendarsAdapter
from app.core.settings import Settings, get_settings

KST = ZoneInfo('Asia/Seoul')
MARKET_CALENDARS = {'US': 'XNYS', 'KR': 'XKRX'}


@dataclass(frozen=True, slots=True)
class CompletedMarketSession:
    """A regular exchange session whose data grace period has elapsed."""

    market_type: str
    expected_session_date: date
    session_close_at: datetime


@dataclass(frozen=True, slots=True)
class MarketContextDraft:
    """Immutable values persisted before a market batch starts collecting data."""

    market_type: str
    expected_session_date: date
    session_close_at: datetime
    news_window_start_at: datetime
    news_window_end_at: datetime


class MarketSessionPolicy:
    """Resolve completed exchange sessions and deterministic news cutoffs."""

    def __init__(
        self,
        *,
        calendar: ExchangeCalendar | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._calendar = calendar or ExchangeCalendarsAdapter()
        configured = settings or get_settings()
        self._data_grace = timedelta(
            minutes=configured.market_session_data_grace_minutes
        )

    def latest_completed_session(
        self,
        *,
        market_type: str,
        as_of: datetime,
    ) -> CompletedMarketSession:
        """Return the latest session closed long enough for provider data to settle."""
        if as_of.tzinfo is None:
            raise ValueError('as_of must be timezone-aware.')
        try:
            calendar_name = MARKET_CALENDARS[market_type]
        except KeyError as exc:
            raise ValueError(f'Unsupported market type: {market_type}') from exc

        as_of_utc = as_of.astimezone(UTC)
        local_day = as_of.astimezone(KST).date()
        session_date = self._calendar.session_on_or_before(calendar_name, local_day)
        session_close_at = self._calendar.session_close(calendar_name, session_date)

        while session_close_at + self._data_grace > as_of_utc:
            session_date = self._calendar.previous_session(calendar_name, session_date)
            session_close_at = self._calendar.session_close(calendar_name, session_date)

        return CompletedMarketSession(
            market_type=market_type,
            expected_session_date=session_date,
            session_close_at=session_close_at,
        )

    def build_context(
        self,
        *,
        market_type: str,
        as_of: datetime,
        previous_coverage_end_at: datetime | None,
    ) -> MarketContextDraft:
        """Build a market context with a half-open persisted news window."""
        if as_of.tzinfo is None:
            raise ValueError('as_of must be timezone-aware.')
        window_end_at = as_of.astimezone(UTC)
        if previous_coverage_end_at is None:
            window_start_at = window_end_at - timedelta(hours=24)
        else:
            if previous_coverage_end_at.tzinfo is None:
                raise ValueError('previous_coverage_end_at must be timezone-aware.')
            window_start_at = previous_coverage_end_at.astimezone(UTC)
            if window_start_at > window_end_at:
                raise ValueError('News coverage watermark cannot be after window end.')

        session = self.latest_completed_session(
            market_type=market_type,
            as_of=window_end_at,
        )
        return MarketContextDraft(
            market_type=market_type,
            expected_session_date=session.expected_session_date,
            session_close_at=session.session_close_at,
            news_window_start_at=window_start_at,
            news_window_end_at=window_end_at,
        )


__all__ = [
    'CompletedMarketSession',
    'MARKET_CALENDARS',
    'MarketContextDraft',
    'MarketSessionPolicy',
]
