from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time
from typing import Any

from app.batch.diagnostics import (
    INDEX_FETCH_FAILED,
    INDEX_FUTURE_SOURCE_DATE,
    INDEX_NONE_COLLECTED,
    INDEX_STALE_SOURCE_DATE,
)
from app.batch.models import BatchExecutionContext
from app.batch.providers.market_index_provider import (
    YFINANCE_PROVIDER_NAME,
    MarketIndexProvider,
)
from app.batch.steps.base import BatchStep, require_repository_session
from app.core.public_diagnostics import (
    EXTERNAL_PROVIDER_FAILURE_MESSAGE,
    public_external_provider_error,
)
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import MarketIndexDailyCreateParams


class CollectMarketIndicesStep(BatchStep):
    step_code = 'COLLECT_MARKET_INDICES'
    started_message = 'Collect market indices step started.'
    completed_message = 'Collect market indices step completed.'

    def __init__(
        self,
        *,
        provider_factory: Callable[[], Any] | None = None,
        index_repo_factory: Callable[[Any], Any] | None = None,
        context_repo_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._provider_factory = provider_factory or MarketIndexProvider
        self._index_repo_factory = index_repo_factory or MarketIndexRepository
        self._context_repo_factory = context_repo_factory or MarketContextRepository

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped market index collection because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)

        provider = self._provider_factory()
        index_repo = self._index_repo_factory(session)
        context_repo = None
        market_context_rows = []
        if self._context_repo_factory is not MarketContextRepository or hasattr(
            session, 'execute'
        ):
            context_repo = self._context_repo_factory(session)
            market_context_rows = await context_repo.list_for_job(context.job_id)
        market_contexts = {row.market_type: row for row in market_context_rows}
        expected_session_dates = {
            market_type: row.expected_session_date
            for market_type, row in market_contexts.items()
        }
        if expected_session_dates:
            results = await provider.fetch_for_business_date(
                context.business_date,
                expected_session_dates=expected_session_dates,
            )
        else:
            results = await provider.fetch_for_business_date(context.business_date)
        failures = list(getattr(provider, 'last_failures', []))
        for failure in failures:
            ticker = _failure_value(failure, 'ticker')
            error_class = _failure_value(failure, 'error_class')
            context.add_partial(
                INDEX_FETCH_FAILED,
                f'Market index collection failed for {ticker}: '
                f'{EXTERNAL_PROVIDER_FAILURE_MESSAGE}',
            )
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='Failed to collect a market index ticker.',
                context_json={
                    'provider': _failure_value(failure, 'provider'),
                    'marketType': _failure_value(failure, 'market_type'),
                    'ticker': ticker,
                    'indexCode': _failure_value(failure, 'index_code'),
                    'indexName': _failure_value(failure, 'index_name'),
                    'error': public_external_provider_error(error_class),
                },
            )
        for fallback in list(getattr(provider, 'last_session_fallbacks', [])):
            # The expected session's daily bar was unreadable. That is worth
            # recording even when the quote block rescued the close, because
            # nothing recorded it before: the provider silently used an older
            # session for twenty-eight days and only the resulting
            # source_date ever hinted at it. Whether the page is degraded is
            # still decided below by comparing the dates -- a recovered
            # reading is not stale.
            recovered = bool(_failure_flag(fallback, 'recovered_from_quote'))
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message=(
                    'Recovered a market index close from the provider quote.'
                    if recovered
                    else 'Fell back to an earlier market index session.'
                ),
                context_json={
                    'provider': _failure_value(fallback, 'provider'),
                    'marketType': _failure_value(fallback, 'market_type'),
                    'indexCode': _failure_value(fallback, 'index_code'),
                    'expectedSessionDate': _failure_value(
                        fallback, 'expected_session_date'
                    ),
                    'usedSourceDate': _failure_value(fallback, 'used_source_date'),
                    'reasonCode': _failure_value(fallback, 'reason_code'),
                    'recoveredFromQuote': recovered,
                },
            )
        if not results:
            context.add_partial(
                INDEX_NONE_COLLECTED, '시장 지수 데이터를 수집하지 못했습니다.'
            )
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='No market indices were collected.',
            )
            return context

        inserted_count = 0
        source_dates_by_market: dict[str, list[date]] = {}
        for result in results:
            market_context = market_contexts.get(result.market_type)
            expected_session_date = (
                market_context.expected_session_date
                if market_context is not None
                else context.business_date
            )
            if result.source_date > expected_session_date:
                context.add_partial(
                    INDEX_FUTURE_SOURCE_DATE,
                    f'{result.market_type}:{result.index_code} returned future '
                    f'source date {result.source_date.isoformat()} after expected '
                    f'session {expected_session_date.isoformat()}.',
                )
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.WARN.value,
                    message='Rejected a future market index source date.',
                    context_json={
                        'marketType': result.market_type,
                        'indexCode': result.index_code,
                        'sourceDate': result.source_date.isoformat(),
                        'expectedSessionDate': expected_session_date.isoformat(),
                    },
                )
                continue

            session_close_at = (
                market_context.session_close_at
                if market_context is not None
                else datetime.combine(
                    expected_session_date,
                    time.min,
                    tzinfo=UTC,
                )
            )
            await index_repo.upsert_index(
                MarketIndexDailyCreateParams(
                    business_date=context.business_date,
                    market_type=result.market_type,
                    source_date=result.source_date,
                    expected_session_date=expected_session_date,
                    session_close_at=session_close_at,
                    index_code=result.index_code,
                    index_name=result.index_name,
                    close_price=result.close_price,
                    change_value=result.change_value,
                    change_percent=result.change_percent,
                    high_price=result.high_price,
                    low_price=result.low_price,
                    currency_code=result.currency_code,
                    provider_name=YFINANCE_PROVIDER_NAME,
                )
            )
            inserted_count += 1
            source_dates_by_market.setdefault(result.market_type, []).append(
                result.source_date
            )
            if result.source_date < expected_session_date:
                context.add_partial(
                    INDEX_STALE_SOURCE_DATE,
                    f'{result.market_type}:{result.index_code} used stale source '
                    f'date {result.source_date.isoformat()} before expected session '
                    f'{expected_session_date.isoformat()}.',
                )
                level = EventLevel.WARN.value
                message = 'Market index source date is stale.'
            else:
                level = EventLevel.INFO.value
                message = 'Market index matched the expected completed session.'
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=level,
                message=message,
                context_json={
                    'marketType': result.market_type,
                    'indexCode': result.index_code,
                    'sourceDate': result.source_date.isoformat(),
                    'expectedSessionDate': expected_session_date.isoformat(),
                    'sessionCloseAt': session_close_at.isoformat(),
                },
            )

        for market_type, source_dates in source_dates_by_market.items():
            if context_repo is None or market_type not in market_contexts:
                continue
            await context_repo.set_actual_index_source_date(
                job_id=context.job_id,
                market_type=market_type,
                source_date=min(source_dates),
            )

        if inserted_count == 0:
            context.add_partial(
                INDEX_NONE_COLLECTED, '시장 지수 데이터를 수집하지 못했습니다.'
            )

        context.collected_index_count += inserted_count
        context.log_messages.append(f'Collected {inserted_count} market index row(s).')
        return context


def _failure_value(failure: object, name: str) -> str:
    if isinstance(failure, dict):
        return str(failure.get(name, ''))
    return str(getattr(failure, name, ''))


def _failure_flag(failure: object, name: str) -> bool:
    if isinstance(failure, dict):
        return bool(failure.get(name, False))
    return bool(getattr(failure, name, False))


__all__ = ['CollectMarketIndicesStep']
