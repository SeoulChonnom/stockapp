from __future__ import annotations

import httpx

from app.batch.exceptions import BatchPipelineError
from app.batch.models import BatchExecutionContext
from app.batch.providers import NAVER_NEWS_PROVIDER_NAME, NaverNewsProvider
from app.batch.steps.base import BatchStep
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.news_search_keyword_repo import NewsSearchKeywordRepository


class CollectNewsStep(BatchStep):
    step_code = 'COLLECT_NEWS'
    started_message = 'Collect news step started.'
    completed_message = 'Collect news step completed.'

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

        provider = NaverNewsProvider()
        if not provider.is_configured():
            raise BatchPipelineError(
                error_code='NAVER_NOT_CONFIGURED',
                error_message='Naver news API credentials are not configured.',
            )

        total_fetched = 0
        total_candidates = 0
        total_inserted = 0

        for keyword in keywords:
            try:
                collection = await provider.collect_for_keyword(
                    keyword_record=keyword,
                    business_date=context.business_date,
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
                    f"'{keyword.keyword}': {exc}"
                )
                if partial_reason not in context.partial_reasons:
                    context.partial_reasons.append(partial_reason)
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.WARN.value,
                    message='Failed to collect Naver news for keyword.',
                    context_json={
                        'provider': keyword.provider_name,
                        'marketType': keyword.market_type,
                        'keyword': keyword.keyword,
                        'error': {
                            'provider': 'NaverNewsProvider',
                            'errorClass': type(exc).__name__,
                            'errorMessage': str(exc),
                        },
                    },
                )
                continue
            inserted_count = await raw_repo.insert_articles(collection.articles)
            total_fetched += collection.fetched_count
            total_candidates += collection.candidate_count
            total_inserted += inserted_count
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
                },
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


def _is_naver_auth_failure(exc: Exception) -> bool:
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {401, 403}
    )


__all__ = ['CollectNewsStep']
