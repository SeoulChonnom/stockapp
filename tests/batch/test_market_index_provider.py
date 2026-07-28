from __future__ import annotations

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
):
    provider = MarketIndexProvider()
    monkeypatch.setattr(provider, '_download_history', lambda *_args: history)
    return await provider._fetch_single(
        business_date=business_date,
        market_type='US',
        ticker='^GSPC',
        index_name='S&P 500',
        currency_code='USD',
        index_code='^GSPC',
    )


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
async def test_fetch_single_ignores_empty_or_missing_close_history(monkeypatch, history):
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
