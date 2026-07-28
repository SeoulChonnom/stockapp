from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol

import exchange_calendars


class ExchangeCalendar(Protocol):
    """Minimal exchange calendar contract used by the batch policy."""

    def session_on_or_before(self, calendar_name: str, day: date) -> date:
        """Return the latest exchange session on or before ``day``."""

    def previous_session(self, calendar_name: str, session_date: date) -> date:
        """Return the exchange session immediately before ``session_date``."""

    def session_close(self, calendar_name: str, session_date: date) -> datetime:
        """Return a session's regular close as an aware UTC datetime."""


class ExchangeCalendarsAdapter:
    """Adapter around exchange-calendars to keep vendor types out of policy code."""

    def session_on_or_before(self, calendar_name: str, day: date) -> date:
        calendar = exchange_calendars.get_calendar(calendar_name)
        session = calendar.date_to_session(day.isoformat(), direction='previous')
        return session.date()

    def previous_session(self, calendar_name: str, session_date: date) -> date:
        calendar = exchange_calendars.get_calendar(calendar_name)
        session = calendar.previous_session(session_date.isoformat())
        return session.date()

    def session_close(self, calendar_name: str, session_date: date) -> datetime:
        calendar = exchange_calendars.get_calendar(calendar_name)
        close = calendar.session_close(session_date.isoformat()).to_pydatetime()
        if close.tzinfo is None:
            return close.replace(tzinfo=UTC)
        return close.astimezone(UTC)


__all__ = ['ExchangeCalendar', 'ExchangeCalendarsAdapter']
