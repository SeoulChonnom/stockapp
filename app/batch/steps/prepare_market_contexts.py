from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.batch.models import BatchExecutionContext
from app.batch.policies.market_session_policy import MarketSessionPolicy
from app.batch.steps.base import BatchStep, require_repository_session
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.projections import BatchJobMarketContextCreateParams

KST = ZoneInfo('Asia/Seoul')
MARKET_TYPES = ('US', 'KR')


class PrepareMarketContextsStep(BatchStep):
    """Persist session and news cutoffs before any provider work starts."""

    step_code = 'PREPARE_MARKET_CONTEXTS'
    started_message = 'Prepare market contexts step started.'
    completed_message = 'Prepare market contexts step completed.'

    def __init__(
        self,
        *,
        now_factory: Callable[[], datetime] | None = None,
        policy_factory: Callable[[], Any] | None = None,
        context_repo_factory: Callable[[object], Any] | None = None,
    ) -> None:
        self._now_factory = now_factory or (lambda: datetime.now(UTC))
        self._policy_factory = policy_factory or MarketSessionPolicy
        self._context_repo_factory = context_repo_factory or MarketContextRepository

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        session = require_repository_session(repository, step_code=self.step_code)
        context_repo = self._context_repo_factory(session)
        existing = {
            row.market_type: row
            for row in await context_repo.list_for_job(context.job_id)
        }
        if all(market_type in existing for market_type in MARKET_TYPES):
            context.log_messages.append('Reused persisted market contexts.')
            return context

        as_of = _cutoff_for_business_date(
            business_date=context.business_date,
            now=self._now_factory(),
        )
        policy = None
        for market_type in MARKET_TYPES:
            if market_type in existing:
                continue

            template = None
            if context.force_run or context.rebuild_page_only:
                template = await context_repo.get_latest_for_business_date(
                    business_date=context.business_date,
                    market_type=market_type,
                    exclude_job_id=context.job_id,
                )

            if template is not None:
                params = BatchJobMarketContextCreateParams(
                    batch_job_id=context.job_id,
                    market_type=market_type,
                    expected_session_date=template.expected_session_date,
                    session_close_at=template.session_close_at,
                    news_window_start_at=template.news_window_start_at,
                    news_window_end_at=template.news_window_end_at,
                )
            else:
                if policy is None:
                    policy = self._policy_factory()
                previous_coverage_end_at = (
                    await context_repo.get_latest_complete_coverage_end(
                        market_type=market_type,
                        at_or_before=as_of,
                    )
                )
                draft = policy.build_context(
                    market_type=market_type,
                    as_of=as_of,
                    previous_coverage_end_at=previous_coverage_end_at,
                )
                params = BatchJobMarketContextCreateParams(
                    batch_job_id=context.job_id,
                    market_type=market_type,
                    expected_session_date=draft.expected_session_date,
                    session_close_at=draft.session_close_at,
                    news_window_start_at=draft.news_window_start_at,
                    news_window_end_at=draft.news_window_end_at,
                )
            await context_repo.insert_if_absent(params)

        context.log_messages.append('Prepared persisted market contexts.')
        return context


def _cutoff_for_business_date(*, business_date: date, now: datetime) -> datetime:
    if now.tzinfo is None:
        raise ValueError('Market context clock must be timezone-aware.')
    now_utc = now.astimezone(UTC)
    if business_date >= now.astimezone(KST).date():
        return now_utc
    next_midnight = datetime.combine(
        business_date + timedelta(days=1),
        time.min,
        tzinfo=KST,
    )
    return next_midnight.astimezone(UTC)


__all__ = ['MARKET_TYPES', 'PrepareMarketContextsStep']
