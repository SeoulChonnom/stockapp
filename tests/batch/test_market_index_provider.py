from __future__ import annotations

import threading
import time
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from app.batch.providers import market_index_provider as provider_module
from app.batch.providers.market_index_provider import MarketIndexProvider
from app.core.settings import Settings


async def _fetch_single_with_history(
    monkeypatch: pytest.MonkeyPatch,
    history: pd.DataFrame,
    *,
    business_date: date = date(2026, 3, 17),
    metadata: dict | None = None,
):
    provider = MarketIndexProvider()
    monkeypatch.setattr(
        provider, '_download_history', lambda *_args: (history, metadata or {})
    )
    return await provider._fetch_single(
        business_date=business_date,
        market_type='US',
        ticker='^GSPC',
        index_name='S&P 500',
        currency_code='USD',
        index_code='^GSPC',
    )


async def _fetch_single_recording_provider(
    monkeypatch: pytest.MonkeyPatch,
    history: pd.DataFrame,
    *,
    business_date: date = date(2026, 3, 17),
    metadata: dict | None = None,
) -> tuple[MarketIndexProvider, object]:
    provider = MarketIndexProvider()
    monkeypatch.setattr(
        provider, '_download_history', lambda *_args: (history, metadata or {})
    )
    result = await provider._fetch_single(
        business_date=business_date,
        market_type='KR',
        ticker='^KS11',
        index_name='KOSPI',
        currency_code='KRW',
        index_code='^KS11',
    )
    return provider, result


def _quote(session_date: str, price: float) -> dict:
    """The quote block yfinance leaves on the same chart response."""
    return {
        'regularMarketTime': pd.Timestamp(f'{session_date} 18:05:40', tz='Asia/Seoul'),
        'regularMarketPrice': price,
    }


_KR_HISTORY_WITH_UNSETTLED_CLOSE = pd.DataFrame(
    {
        'Open': [6910.78, 7045.79],
        'Close': [6995.39, float('nan')],
        'High': [6995.40, 7171.52],
        'Low': [6900.00, 7000.00],
    },
    index=pd.to_datetime(['2026-09-07', '2026-09-08']),
)


@pytest.mark.anyio
async def test_fetch_single_reads_the_expected_session_close_from_the_quote(
    monkeypatch,
):
    """job 1693's shape: Yahoo had not settled the 09-08 daily close yet.

    The daily bar for the expected session exists but carries no close for
    hours after the session ends, while the quote block on the same response
    already holds the settled value -- 6954.52 here, which is what the daily
    bar was eventually filled with. Recovering it keeps the page on the
    session it asked for instead of silently showing an older one.
    """
    provider, result = await _fetch_single_recording_provider(
        monkeypatch,
        _KR_HISTORY_WITH_UNSETTLED_CLOSE,
        business_date=date(2026, 9, 8),
        metadata=_quote('2026-09-08', 6954.52),
    )

    assert result is not None
    assert result.source_date == date(2026, 9, 8)
    assert result.close_price == Decimal('6954.5200')
    # measured against 09-07's close, the session actually before it
    assert result.change_value == Decimal('-40.8700')
    assert [
        (
            fallback.index_code,
            fallback.reason_code,
            fallback.expected_session_date,
            fallback.used_source_date,
            fallback.recovered_from_quote,
        )
        for fallback in provider.last_session_fallbacks
    ] == [
        (
            '^KS11',
            provider_module.EXPECTED_SESSION_CLOSE_NOT_FINITE,
            date(2026, 9, 8),
            date(2026, 9, 8),
            True,
        )
    ]


@pytest.mark.anyio
async def test_fetch_single_refuses_a_quote_stamped_with_another_session(monkeypatch):
    """A live session's quote must never be written in as a daily close.

    While the market is open the quote block describes the day in progress,
    not the completed session the page wants. Checked against the real
    response on 2026-09-09, ``regularMarketPrice`` was an intraday 7051.64
    stamped 09-09 while the batch wanted 09-08 -- taking it would have
    recorded an intraday value as a close.
    """
    provider, result = await _fetch_single_recording_provider(
        monkeypatch,
        _KR_HISTORY_WITH_UNSETTLED_CLOSE,
        business_date=date(2026, 9, 8),
        metadata=_quote('2026-09-09', 7051.64),
    )

    assert result is not None
    assert result.source_date == date(2026, 9, 7)
    assert result.close_price == Decimal('6995.3900')
    assert provider.last_session_fallbacks[0].recovered_from_quote is False
    assert (
        provider.last_session_fallbacks[0].reason_code
        == provider_module.EXPECTED_SESSION_CLOSE_NOT_FINITE
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    'metadata',
    [
        {},
        {'regularMarketTime': 1788858340, 'regularMarketPrice': 6954.52},
        _quote('2026-09-08', float('nan')),
        _quote('2026-09-08', 0.0),
    ],
    ids=['no_quote', 'timestamp_is_not_a_timestamp', 'non_finite_price', 'zero_price'],
)
async def test_fetch_single_falls_back_visibly_when_the_quote_is_unusable(
    monkeypatch, metadata
):
    provider, result = await _fetch_single_recording_provider(
        monkeypatch,
        _KR_HISTORY_WITH_UNSETTLED_CLOSE,
        business_date=date(2026, 9, 8),
        metadata=metadata,
    )

    assert result is not None
    assert result.source_date == date(2026, 9, 7)
    assert provider.last_session_fallbacks[0].recovered_from_quote is False


@pytest.mark.anyio
async def test_fetch_single_reports_a_missing_expected_session_row(monkeypatch):
    history = pd.DataFrame(
        {'Open': [6910.78], 'Close': [6995.39], 'High': [6995.4], 'Low': [6900.0]},
        index=pd.to_datetime(['2026-09-07']),
    )

    provider, result = await _fetch_single_recording_provider(
        monkeypatch, history, business_date=date(2026, 9, 8), metadata={}
    )

    assert result is not None
    assert result.source_date == date(2026, 9, 7)
    assert (
        provider.last_session_fallbacks[0].reason_code
        == provider_module.EXPECTED_SESSION_ROW_MISSING
    )


@pytest.mark.anyio
async def test_fetch_single_records_nothing_when_the_expected_session_reads_cleanly(
    monkeypatch,
):
    history = pd.DataFrame(
        {
            'Open': [6910.78, 7045.79],
            'Close': [6995.39, 6954.52],
            'High': [6995.4, 7171.52],
            'Low': [6900.0, 6900.0],
        },
        index=pd.to_datetime(['2026-09-07', '2026-09-08']),
    )

    provider, result = await _fetch_single_recording_provider(
        monkeypatch,
        history,
        business_date=date(2026, 9, 8),
        metadata=_quote('2026-09-08', 6954.52),
    )

    assert result is not None
    assert result.source_date == date(2026, 9, 8)
    assert provider.last_session_fallbacks == []


@pytest.mark.anyio
async def test_fetch_for_business_date_records_download_timeout(monkeypatch):
    provider = MarketIndexProvider(Settings(yfinance_timeout_seconds=0.25))
    monkeypatch.setattr(
        provider_module,
        'MARKET_INDEX_TICKERS',
        {'US': [('TIMEOUT', 'Timed Out Index', 'USD', 'TIMEOUT')]},
    )

    download_calls = []

    async def blocked_download():
        raise AssertionError('blocked download must not run')

    def fake_to_thread(function, *args):
        download_calls.append((function, args))
        return blocked_download()

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        assert timeout == 0.25
        raise TimeoutError('ticker download timed out')

    monkeypatch.setattr(provider_module.asyncio, 'to_thread', fake_to_thread)
    monkeypatch.setattr(provider_module.asyncio, 'wait_for', fake_wait_for)

    results = await provider.fetch_for_business_date(date(2026, 3, 17))

    assert results == []
    assert download_calls == [
        (
            provider._download_history,
            ('TIMEOUT', date(2026, 3, 10), date(2026, 3, 18)),
        )
    ]
    assert provider.last_failures[0].error_class == 'TimeoutError'
    assert provider.last_failures[0].error_message == 'ticker download timed out'


@pytest.mark.anyio
@pytest.mark.parametrize(
    'history',
    [
        pd.DataFrame(),
        pd.DataFrame(
            {'Open': [100.0], 'High': [101.0], 'Low': [99.0]},
            index=pd.to_datetime(['2026-03-17']),
        ),
    ],
    ids=['empty_history', 'missing_close_column'],
)
async def test_fetch_single_ignores_empty_or_missing_close_history(
    monkeypatch, history
):
    result = await _fetch_single_with_history(monkeypatch, history)

    assert result is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    'history',
    [
        pd.DataFrame(
            {'Open': [100.0], 'Close': [float('nan')]},
            index=pd.to_datetime(['2026-03-17']),
        ),
        pd.DataFrame(
            {'Open': [float('inf')], 'Close': [100.0]},
            index=pd.to_datetime(['2026-03-17']),
        ),
    ],
    ids=['non_finite_close', 'non_finite_open_fallback'],
)
async def test_fetch_single_ignores_non_finite_required_prices(monkeypatch, history):
    result = await _fetch_single_with_history(monkeypatch, history)

    assert result is None


@pytest.mark.anyio
async def test_fetch_single_omits_non_finite_high_and_low_prices(monkeypatch):
    history = pd.DataFrame(
        {
            'Open': [99.0],
            'Close': [100.0],
            'High': [float('nan')],
            'Low': [float('-inf')],
        },
        index=pd.to_datetime(['2026-03-17']),
    )

    result = await _fetch_single_with_history(monkeypatch, history)

    assert result is not None
    assert result.high_price is None
    assert result.low_price is None


@pytest.mark.anyio
async def test_fetch_single_uses_latest_prior_trading_date_for_holiday(monkeypatch):
    history = pd.DataFrame(
        {
            'Open': [98.0, 100.0],
            'Close': [100.0, 101.5],
            'High': [101.0, 102.0],
            'Low': [97.0, 99.0],
        },
        index=pd.to_datetime(['2026-03-13', '2026-03-16']),
    )

    result = await _fetch_single_with_history(
        monkeypatch,
        history,
        business_date=date(2026, 3, 17),
    )

    assert result is not None
    assert result.source_date == date(2026, 3, 16)
    assert result.close_price == Decimal('101.5000')
    assert result.change_value == Decimal('1.5000')
    assert result.change_percent == Decimal('1.5000')


@pytest.mark.anyio
async def test_fetch_single_skips_latest_row_with_non_finite_close(monkeypatch):
    history = pd.DataFrame(
        {
            'Open': [98.0, 100.0, 102.0],
            'Close': [100.0, 101.5, float('nan')],
            'High': [101.0, 102.0, 103.0],
            'Low': [97.0, 99.0, 101.0],
        },
        index=pd.to_datetime(['2026-03-13', '2026-03-16', '2026-03-17']),
    )

    result = await _fetch_single_with_history(monkeypatch, history)

    assert result is not None
    assert result.source_date == date(2026, 3, 16)
    assert result.close_price == Decimal('101.5000')
    assert result.change_value == Decimal('1.5000')


@pytest.mark.anyio
async def test_fetch_single_sorts_history_before_selecting_latest_finite_row(
    monkeypatch,
):
    history = pd.DataFrame(
        {
            'Open': [100.0, 98.0],
            'Close': [101.5, 100.0],
            'High': [102.0, 101.0],
            'Low': [99.0, 97.0],
        },
        index=pd.to_datetime(['2026-03-16', '2026-03-13']),
    )

    result = await _fetch_single_with_history(monkeypatch, history)

    assert result is not None
    assert result.source_date == date(2026, 3, 16)
    assert result.close_price == Decimal('101.5000')
    assert result.change_value == Decimal('1.5000')


_TWO_CLEAN_SESSIONS = pd.DataFrame(
    {
        'Open': [98.0, 100.0],
        'Close': [100.0, 101.5],
        'High': [101.0, 102.0],
        'Low': [97.0, 99.0],
    },
    index=pd.to_datetime(['2026-03-16', '2026-03-17']),
)


def _configured_ticker_count() -> int:
    return sum(len(rows) for rows in provider_module.MARKET_INDEX_TICKERS.values())


@pytest.mark.anyio
async def test_fetch_for_business_date_never_downloads_two_tickers_at_once(monkeypatch):
    """Downloads must not overlap, because yfinance's tz cache cannot take it.

    Each download opens a connection to a SQLite cache shared by the whole
    process, and that connection runs ``PRAGMA journal_mode = wal``, which
    needs the file exclusively. Two at once make the loser raise
    ``OperationalError: database is locked``, which yfinance neither retries
    nor handles -- in production that silently cost an index its card three
    times, on a different ticker each time.
    """
    provider = MarketIndexProvider()
    guard = threading.Lock()
    in_flight = 0
    peak_in_flight = 0

    def tracked_download(_ticker, _start_date, _end_date):
        nonlocal in_flight, peak_in_flight
        with guard:
            in_flight += 1
            peak_in_flight = max(peak_in_flight, in_flight)
        time.sleep(0.02)
        with guard:
            in_flight -= 1
        return _TWO_CLEAN_SESSIONS, {}

    monkeypatch.setattr(provider, '_download_history', tracked_download)

    results = await provider.fetch_for_business_date(date(2026, 3, 17))

    assert len(results) == _configured_ticker_count()
    assert peak_in_flight == 1


@pytest.mark.anyio
async def test_fetch_for_business_date_keeps_going_after_one_ticker_raises(monkeypatch):
    """One ticker blowing up must not cost the other four their cards."""
    provider = MarketIndexProvider()

    def flaky_download(ticker, _start_date, _end_date):
        if ticker == '^KS11':
            raise RuntimeError('database is locked')
        return _TWO_CLEAN_SESSIONS, {}

    monkeypatch.setattr(provider, '_download_history', flaky_download)

    results = await provider.fetch_for_business_date(date(2026, 3, 17))

    assert len(results) == _configured_ticker_count() - 1
    assert '^KS11' not in {result.index_code for result in results}
    assert [
        (failure.index_code, failure.error_class) for failure in provider.last_failures
    ] == [('^KS11', 'RuntimeError')]
