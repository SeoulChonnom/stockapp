from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

YFINANCE_PROVIDER_NAME = 'YFINANCE'

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


class MarketIndexProvider:
    async def fetch_for_business_date(
        self, business_date: date
    ) -> list[MarketIndexFetchResult]:
        tasks = [
            self._fetch_single(
                business_date=business_date,
                market_type=market_type,
                ticker=ticker,
                index_name=index_name,
                currency_code=currency_code,
                index_code=index_code,
            )
            for market_type, rows in MARKET_INDEX_TICKERS.items()
            for ticker, index_name, currency_code, index_code in rows
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            result for result in results if isinstance(result, MarketIndexFetchResult)
        ]

    async def _fetch_single(
        self,
        *,
        business_date: date,
        market_type: str,
        ticker: str,
        index_name: str,
        currency_code: str,
        index_code: str,
    ) -> MarketIndexFetchResult | None:
        history = await asyncio.to_thread(
            self._download_history,
            ticker,
            business_date - timedelta(days=7),
            business_date + timedelta(days=1),
        )
        if history.empty:
            return None

        selected = history[history.index.date <= business_date]
        if selected.empty:
            return None

        row = selected.iloc[-1]
        current_close = row.get('Close')
        if current_close is None:
            return None
        previous_close = None
        if len(selected.index) >= 2:
            previous_close = selected.iloc[-2].get('Close')
        if previous_close is None:
            previous_close = row.get('Open')
        if previous_close is None:
            return None

        close_price = Decimal(str(current_close))
        previous_close_decimal = Decimal(str(previous_close))
        change_value = close_price - previous_close_decimal
        change_percent = Decimal('0')
        if previous_close_decimal != 0:
            change_percent = (change_value / previous_close_decimal) * Decimal('100')

        high_price = row.get('High')
        low_price = row.get('Low')
        source_date = selected.index[-1].date()
        return MarketIndexFetchResult(
            market_type=market_type,
            index_code=index_code,
            index_name=index_name,
            currency_code=currency_code,
            source_date=source_date,
            close_price=close_price.quantize(Decimal('0.0001')),
            change_value=change_value.quantize(Decimal('0.0001')),
            change_percent=change_percent.quantize(Decimal('0.0001')),
            high_price=Decimal(str(high_price)).quantize(Decimal('0.0001'))
            if high_price is not None
            else None,
            low_price=Decimal(str(low_price)).quantize(Decimal('0.0001'))
            if low_price is not None
            else None,
        )

    @staticmethod
    def _download_history(ticker: str, start_date: date, end_date: date):
        return yf.Ticker(ticker).history(
            start=start_date.isoformat(), end=end_date.isoformat(), auto_adjust=False
        )


__all__ = [
    'MARKET_INDEX_TICKERS',
    'MarketIndexFetchResult',
    'MarketIndexProvider',
    'YFINANCE_PROVIDER_NAME',
]
