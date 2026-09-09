from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

import yfinance as yf

from app.core.settings import Settings, get_settings

YFINANCE_PROVIDER_NAME = 'YFINANCE'

# Why the expected session's daily bar could not be read. Fixed vocabulary:
# these codes travel into batch event payloads, never provider response text.
EXPECTED_SESSION_CLOSE_NOT_FINITE = 'EXPECTED_SESSION_CLOSE_NOT_FINITE'
EXPECTED_SESSION_ROW_MISSING = 'EXPECTED_SESSION_ROW_MISSING'

MARKET_INDEX_TICKERS: dict[str, list[tuple[str, str, str, str]]] = {
    'US': [
        ('^GSPC', 'S&P 500', 'USD', '^GSPC'),
        ('^IXIC', 'NASDAQ', 'USD', '^IXIC'),
        ('^DJI', 'Dow Jones', 'USD', '^DJI'),
    ],
    'KR': [
        ('^KS11', 'KOSPI', 'KRW', '^KS11'),
        ('^KQ11', 'KOSDAQ', 'KRW', '^KQ11'),
    ],
}


@dataclass(slots=True)
class MarketIndexFetchResult:
    market_type: str
    index_code: str
    index_name: str
    currency_code: str
    source_date: date
    close_price: Decimal
    change_value: Decimal
    change_percent: Decimal
    high_price: Decimal | None
    low_price: Decimal | None


@dataclass(slots=True)
class MarketIndexSessionFallback:
    """One index whose expected session could not be read from the daily bars.

    Recorded whether or not the quote block rescued the close, because the
    daily bar being unreadable is itself the condition worth watching: it
    went unnoticed for twenty-eight days precisely because nothing recorded
    it.  ``used_source_date`` equals ``expected_session_date`` when the
    reading was recovered.
    """

    provider: str
    market_type: str
    index_code: str
    index_name: str
    expected_session_date: date
    used_source_date: date
    reason_code: str
    recovered_from_quote: bool


@dataclass(slots=True)
class _SessionReading:
    """The prices a single index contributes, and how they were obtained."""

    source_date: date
    close_price: Decimal
    previous_close: Decimal
    high_price: Decimal | None
    low_price: Decimal | None
    unusable_reason_code: str | None
    recovered_from_quote: bool


@dataclass(slots=True)
class MarketIndexFailureDetail:
    provider: str
    market_type: str
    ticker: str
    index_code: str
    index_name: str
    error_class: str
    error_message: str


class MarketIndexProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.last_failures: list[MarketIndexFailureDetail] = []
        self.last_session_fallbacks: list[MarketIndexSessionFallback] = []

    async def fetch_for_business_date(
        self,
        business_date: date,
        *,
        expected_session_dates: dict[str, date] | None = None,
    ) -> list[MarketIndexFetchResult]:
        descriptors = [
            {
                'market_type': market_type,
                'ticker': ticker,
                'index_name': index_name,
                'currency_code': currency_code,
                'index_code': index_code,
            }
            for market_type, rows in MARKET_INDEX_TICKERS.items()
            for ticker, index_name, currency_code, index_code in rows
        ]
        tasks = [
            self._fetch_single(
                expected_session_date=(expected_session_dates or {}).get(
                    descriptor['market_type'],
                    business_date,
                ),
                market_type=descriptor['market_type'],
                ticker=descriptor['ticker'],
                index_name=descriptor['index_name'],
                currency_code=descriptor['currency_code'],
                index_code=descriptor['index_code'],
            )
            for descriptor in descriptors
        ]
        self.last_session_fallbacks = []
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.last_failures = [
            MarketIndexFailureDetail(
                provider=YFINANCE_PROVIDER_NAME,
                market_type=descriptor['market_type'],
                ticker=descriptor['ticker'],
                index_code=descriptor['index_code'],
                index_name=descriptor['index_name'],
                error_class=(
                    type(result).__name__
                    if isinstance(result, Exception)
                    else 'MissingMarketIndexData'
                ),
                error_message=(
                    str(result)
                    if isinstance(result, Exception)
                    else 'No valid row was returned at or before the expected session.'
                ),
            )
            for descriptor, result in zip(descriptors, results, strict=True)
            if isinstance(result, Exception) or result is None
        ]
        return [
            result for result in results if isinstance(result, MarketIndexFetchResult)
        ]

    async def _fetch_single(
        self,
        *,
        expected_session_date: date | None = None,
        business_date: date | None = None,
        market_type: str,
        ticker: str,
        index_name: str,
        currency_code: str,
        index_code: str,
    ) -> MarketIndexFetchResult | None:
        target_date = expected_session_date or business_date
        if target_date is None:
            raise ValueError('An expected session date is required.')
        history, metadata = await asyncio.wait_for(
            asyncio.to_thread(
                self._download_history,
                ticker,
                target_date - timedelta(days=7),
                target_date + timedelta(days=1),
            ),
            timeout=self._settings.yfinance_timeout_seconds,
        )
        if history.empty:
            return None

        index_dates = cast(Any, history.index).date
        selected = history[index_dates <= target_date].sort_index()
        if selected.empty:
            return None

        valid_rows = [
            (row_index, row, close_price)
            for row_index, row in selected.iterrows()
            if (close_price := self._to_finite_decimal(row.get('Close'))) is not None
        ]
        if not valid_rows:
            return None
        reading = self._read_target_session(
            selected=selected,
            valid_rows=valid_rows,
            metadata=metadata,
            target_date=target_date,
        )
        if reading is None:
            return None
        if reading.unusable_reason_code is not None:
            self.last_session_fallbacks.append(
                MarketIndexSessionFallback(
                    provider=YFINANCE_PROVIDER_NAME,
                    market_type=market_type,
                    index_code=index_code,
                    index_name=index_name,
                    expected_session_date=target_date,
                    used_source_date=reading.source_date,
                    reason_code=reading.unusable_reason_code,
                    recovered_from_quote=reading.recovered_from_quote,
                )
            )

        change_value = reading.close_price - reading.previous_close
        change_percent = Decimal('0')
        if reading.previous_close != 0:
            change_percent = (change_value / reading.previous_close) * Decimal('100')

        high_price = reading.high_price
        low_price = reading.low_price
        return MarketIndexFetchResult(
            market_type=market_type,
            index_code=index_code,
            index_name=index_name,
            currency_code=currency_code,
            source_date=reading.source_date,
            close_price=reading.close_price.quantize(Decimal('0.0001')),
            change_value=change_value.quantize(Decimal('0.0001')),
            change_percent=change_percent.quantize(Decimal('0.0001')),
            high_price=high_price.quantize(Decimal('0.0001'))
            if high_price is not None
            else None,
            low_price=low_price.quantize(Decimal('0.0001'))
            if low_price is not None
            else None,
        )

    def _read_target_session(
        self,
        *,
        selected: Any,
        valid_rows: list[tuple[Any, Any, Decimal]],
        metadata: Mapping[str, Any],
        target_date: date,
    ) -> _SessionReading | None:
        """Decide which session's prices this index contributes, and from where.

        The newest usable daily bar is the answer whenever it *is* the
        expected session.  When it is not, the expected session's bar was
        either absent or carried a close Yahoo had not settled yet, and the
        quote block in the same response is consulted before falling back a
        session.
        """
        newest_index, _newest_row, newest_close = valid_rows[-1]
        if cast(Any, newest_index).date() == target_date:
            return self._reading_from_rows(valid_rows, unusable_reason_code=None)

        target_row = self._row_for_date(selected, target_date)
        reason_code = (
            EXPECTED_SESSION_ROW_MISSING
            if target_row is None
            else EXPECTED_SESSION_CLOSE_NOT_FINITE
        )
        quote_close = self._quote_close_for_session(metadata, target_date)
        if quote_close is None:
            return self._reading_from_rows(valid_rows, unusable_reason_code=reason_code)
        return _SessionReading(
            source_date=target_date,
            close_price=quote_close,
            previous_close=newest_close,
            high_price=(
                None
                if target_row is None
                else self._to_finite_decimal(target_row.get('High'))
            ),
            low_price=(
                None
                if target_row is None
                else self._to_finite_decimal(target_row.get('Low'))
            ),
            unusable_reason_code=reason_code,
            recovered_from_quote=True,
        )

    @classmethod
    def _reading_from_rows(
        cls,
        valid_rows: list[tuple[Any, Any, Decimal]],
        *,
        unusable_reason_code: str | None,
    ) -> _SessionReading | None:
        source_index, row, current_close = valid_rows[-1]
        previous_close = None
        if len(valid_rows) >= 2:
            previous_close = valid_rows[-2][2]
        if previous_close is None:
            previous_close = cls._to_finite_decimal(row.get('Open'))
        if previous_close is None:
            return None
        return _SessionReading(
            source_date=cast(Any, source_index).date(),
            close_price=current_close,
            previous_close=previous_close,
            high_price=cls._to_finite_decimal(row.get('High')),
            low_price=cls._to_finite_decimal(row.get('Low')),
            unusable_reason_code=unusable_reason_code,
            recovered_from_quote=False,
        )

    @staticmethod
    def _row_for_date(selected: Any, target_date: date) -> Any | None:
        for row_index, row in selected.iterrows():
            if cast(Any, row_index).date() == target_date:
                return row
        return None

    @classmethod
    def _quote_close_for_session(
        cls, metadata: Mapping[str, Any], target_date: date
    ) -> Decimal | None:
        """Read the expected session's close from the response's quote block.

        Yahoo leaves the newest daily bar's close unsettled for hours after
        a session ends -- for the KR indices, well past this batch's own run
        time -- while the quote block in the very same chart response already
        carries the final value.

        It is usable only when the quote's own timestamp says it describes
        the session we asked for.  During a live session that timestamp is
        the current date, and taking the price then would write an intraday
        value into a daily close, so a mismatch means no answer rather than
        a guess.  That date check is load-bearing together with
        ``MarketSessionPolicy.latest_completed_session``, which only ever
        hands out a session that closed at least the configured grace period
        ago -- were the caller to ask about a session still trading, a
        matching timestamp would no longer mean a settled close.  Anything other than a timestamp that can state its own
        date is treated the same way: the caller then falls back a session
        and records why, which is visible rather than silent.
        """
        stamped_at = metadata.get('regularMarketTime')
        session_date = getattr(stamped_at, 'date', None)
        if not callable(session_date) or session_date() != target_date:
            return None
        close_price = cls._to_finite_decimal(metadata.get('regularMarketPrice'))
        if close_price is None or close_price <= 0:
            return None
        return close_price

    @staticmethod
    def _to_finite_decimal(value: object) -> Decimal | None:
        if value is None:
            return None
        try:
            decimal_value = Decimal(str(value))
        except ArithmeticError, ValueError:
            return None
        return decimal_value if decimal_value.is_finite() else None

    @staticmethod
    def _download_history(ticker: str, start_date: date, end_date: date):
        """Return the daily bars and the quote block from one chart request.

        ``history_metadata`` is what the same response already carried, so
        reading the quote from it costs no second call to the provider.
        """
        handle = yf.Ticker(ticker)
        history = handle.history(
            start=start_date.isoformat(), end=end_date.isoformat(), auto_adjust=False
        )
        metadata = getattr(handle, 'history_metadata', None)
        return history, (metadata if isinstance(metadata, Mapping) else {})


__all__ = [
    'EXPECTED_SESSION_CLOSE_NOT_FINITE',
    'EXPECTED_SESSION_ROW_MISSING',
    'MARKET_INDEX_TICKERS',
    'MarketIndexFailureDetail',
    'MarketIndexFetchResult',
    'MarketIndexProvider',
    'MarketIndexSessionFallback',
    'YFINANCE_PROVIDER_NAME',
]
