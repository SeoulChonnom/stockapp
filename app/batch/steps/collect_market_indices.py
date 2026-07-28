from __future__ import annotations

from collections.abc import Callable

from app.batch.models import BatchExecutionContext
from app.batch.providers.market_index_provider import (
    YFINANCE_PROVIDER_NAME,
    MarketIndexProvider,
)
from app.batch.steps.base import BatchStep, require_repository_session
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import MarketIndexDailyCreateParams


class CollectMarketIndicesStep(BatchStep):
    step_code = 'COLLECT_MARKET_INDICES'
    started_message = 'Collect market indices step started.'
    completed_message = 'Collect market indices step completed.'

    def __init__(
        self,
        *,
        provider_factory: Callable[[], object] | None = None,
        index_repo_factory: Callable[[object], object] | None = None,
    ) -> None:
        self._provider_factory = provider_factory or MarketIndexProvider
        self._index_repo_factory = index_repo_factory or MarketIndexRepository

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
        results = await provider.fetch_for_business_date(context.business_date)
        failures = list(getattr(provider, 'last_failures', []))
        for failure in failures:
            ticker = _failure_value(failure, 'ticker')
            error_class = _failure_value(failure, 'error_class')
            error_message = _failure_value(failure, 'error_message')
            partial_reason = (
                f'Market index collection failed for {ticker}: '
                f'{error_class}: {error_message}'
            )
            if partial_reason not in context.partial_reasons:
                context.partial_reasons.append(partial_reason)
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
                    'error': {
                        'errorClass': error_class,
                        'errorMessage': error_message,
                    },
                },
            )
        if not results:
            context.partial_reasons.append('시장 지수 데이터를 수집하지 못했습니다.')
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='No market indices were collected.',
            )
            return context

        inserted_count = 0
        for result in results:
            await index_repo.upsert_index(
                MarketIndexDailyCreateParams(
                    business_date=context.business_date,
                    market_type=result.market_type,
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
            if result.source_date != context.business_date:
                context.warning_messages.append(
                    f'{result.market_type}:{result.index_code} used fallback '
                    f'trading date {result.source_date.isoformat()}.'
                )

        context.collected_index_count += inserted_count
        context.log_messages.append(f'Collected {inserted_count} market index row(s).')
        return context


def _failure_value(failure: object, name: str) -> str:
    if isinstance(failure, dict):
        return str(failure.get(name, ''))
    return str(getattr(failure, name, ''))


__all__ = ['CollectMarketIndicesStep']
