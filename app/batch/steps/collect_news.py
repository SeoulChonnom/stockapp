from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, TypeGuard
from zoneinfo import ZoneInfo

import httpx

from app.batch.exceptions import BatchPipelineError
from app.batch.models import BatchExecutionContext
from app.batch.providers import NAVER_NEWS_PROVIDER_NAME, NaverNewsProvider
from app.batch.steps.base import BatchStep
from app.core.public_diagnostics import (
    EXTERNAL_PROVIDER_FAILURE_MESSAGE,
    public_external_provider_error,
)
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.news_search_keyword_repo import NewsSearchKeywordRepository


class CollectNewsStep(BatchStep):
    step_code = 'COLLECT_NEWS'
    started_message = 'Collect news step started.'
    completed_message = 'Collect news step completed.'

    def __init__(
        self,
        *,
        provider_factory: type | None = None,
        context_repo_factory: type | None = None,
    ) -> None:
        self._provider_factory = provider_factory
        self._context_repo_factory = context_repo_factory

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped news collection because rebuild_page_only=true.'
            )
            return context

        keyword_repo = NewsSearchKeywordRepository(repository.session)
        raw_repo = NewsArticleRawRepository(repository.session)
        keywords = await keyword_repo.list_active_keywords(
            provider_name=NAVER_NEWS_PROVIDER_NAME
        )
        if not keywords:
            raise BatchPipelineError(
                error_code='NEWS_KEYWORDS_NOT_CONFIGURED',
                error_message='Naver news keywords are not configured.',
            )

        provider = (
            self._provider_factory() if self._provider_factory else NaverNewsProvider()
        )
        if not provider.is_configured():
            raise BatchPipelineError(
                error_code='NAVER_NOT_CONFIGURED',
                error_message='Naver news API credentials are not configured.',
            )

        context_repo = None
        persisted_contexts = []
        if self._context_repo_factory is not None or hasattr(
            repository.session, 'execute'
        ):
            context_repo = (self._context_repo_factory or MarketContextRepository)(
                repository.session
            )
            persisted_contexts = await context_repo.list_for_job(context.job_id)
        persisted_by_market = {row.market_type: row for row in persisted_contexts}

        total_fetched = 0
        total_candidates = 0
        total_inserted = 0
        market_has_keyword = {market_type: False for market_type in persisted_by_market}
        market_coverage_complete = {
            market_type: True for market_type in persisted_by_market
        }

        for keyword in keywords:
            market_has_keyword[keyword.market_type] = True
            window = persisted_by_market.get(keyword.market_type)
            window_start_at, window_end_at = _news_window(
                business_date=context.business_date,
                persisted_context=window,
            )
            try:
                collection = await provider.collect_for_keyword(
                    keyword_record=keyword,
                    business_date=context.business_date,
                    window_start_at=window_start_at,
                    window_end_at=window_end_at,
                )
            except Exception as exc:
                if _is_naver_auth_failure(exc):
                    status_code = exc.response.status_code
                    raise BatchPipelineError(
                        error_code='NAVER_AUTH_FAILED',
                        error_message=(
                            'Naver news API authentication failed '
                            f'(HTTP {status_code}).'
                        ),
                    ) from exc
                warning_message = (
                    f'Failed to collect Naver news for keyword: {keyword.keyword}'
                )
                context.warning_messages.append(warning_message)
                partial_reason = (
                    f'Naver news collection failed for keyword '
                    f"'{keyword.keyword}': {EXTERNAL_PROVIDER_FAILURE_MESSAGE}"
                )
                if partial_reason not in context.partial_reasons:
                    context.partial_reasons.append(partial_reason)
                market_coverage_complete[keyword.market_type] = False
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.WARN.value,
                    message='Failed to collect Naver news for keyword.',
                    context_json={
                        'provider': keyword.provider_name,
                        'marketType': keyword.market_type,
                        'keyword': keyword.keyword,
                        'error': public_external_provider_error(type(exc).__name__),
                    },
                )
                continue
            inserted_count = await raw_repo.insert_articles(collection.articles)
            total_fetched += collection.fetched_count
            total_candidates += collection.candidate_count
            total_inserted += inserted_count
            coverage_complete = bool(getattr(collection, 'coverage_complete', True))
            if not coverage_complete:
                market_coverage_complete[keyword.market_type] = False
                partial_reason = (
                    'Naver news pagination cap was reached before covering '
                    f"the persisted window for keyword '{keyword.keyword}'."
                )
                if partial_reason not in context.partial_reasons:
                    context.partial_reasons.append(partial_reason)
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.INFO.value,
                message='Collected Naver news for keyword.',
                context_json={
                    'provider': keyword.provider_name,
                    'marketType': keyword.market_type,
                    'keyword': keyword.keyword,
                    'fetchedCount': collection.fetched_count,
                    'candidateCount': collection.candidate_count,
                    'insertedCount': inserted_count,
                    'coverageComplete': coverage_complete,
                },
            )

        for market_type in persisted_by_market:
            if context_repo is None:
                continue
            coverage_complete = bool(
                market_has_keyword.get(market_type)
                and market_coverage_complete.get(market_type)
            )
            await context_repo.set_news_coverage_complete(
                job_id=context.job_id,
                market_type=market_type,
                coverage_complete=coverage_complete,
            )

        context.raw_news_count = await raw_repo.count_articles_by_business_date(
            context.business_date
        )
        context.log_messages.append(
            'Collected raw news from Naver '
            f'(fetched={total_fetched}, matched={total_candidates}, '
            f'inserted={total_inserted}).'
        )
        return context


def _is_naver_auth_failure(exc: Exception) -> TypeGuard[httpx.HTTPStatusError]:
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {
        401,
        403,
    }


def _news_window(
    *,
    business_date: date,
    persisted_context: Any | None,
) -> tuple[datetime, datetime]:
    if persisted_context is not None:
        return (
            persisted_context.news_window_start_at,
            persisted_context.news_window_end_at,
        )
    kst = ZoneInfo('Asia/Seoul')
    start_at = datetime.combine(business_date, time.min, tzinfo=kst)
    return start_at, start_at + timedelta(days=1)


__all__ = ['CollectNewsStep']
