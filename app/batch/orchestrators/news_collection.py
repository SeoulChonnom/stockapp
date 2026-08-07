from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.batch.exceptions import BatchLeaseLostError
from app.batch.logging import log_safe_exception
from app.batch.providers.naver_news import (
    NAVER_NEWS_PROVIDER_NAME,
    NaverNewsProvider,
)
from app.db.enums import BatchJobStatus, BatchStepStatus, EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.news_collection_run_repo import NewsCollectionRunRepository
from app.db.repositories.news_search_keyword_repo import NewsSearchKeywordRepository
from app.db.repositories.projections import (
    NewsCollectionKeywordDiagnosticParams,
    NewsSearchKeywordRecord,
)
from app.db.session import get_session_maker

LOGGER = logging.getLogger(__name__)


class NaverRetryableError(RuntimeError):
    """Sanitized signal that lets the durable worker retry a transient outage."""

    def __init__(self) -> None:
        super().__init__('Temporary Naver provider failure; retry scheduled.')


class NaverNewsCollectionOrchestrator:
    def __init__(
        self,
        session_maker: async_sessionmaker | None = None,
        *,
        provider_factory: Callable[[], NaverNewsProvider] | None = None,
    ) -> None:
        self._session_maker = session_maker or get_session_maker()
        self._provider_factory = provider_factory or NaverNewsProvider

    async def run(self, job_id: int, lease_token: UUID | None = None) -> None:
        async with self._session_maker() as session:
            job_repo = BatchJobRepository(session, lease_token=lease_token)
            run_repo = NewsCollectionRunRepository(session)
            keyword_repo = NewsSearchKeywordRepository(session)
            raw_repo = NewsArticleRawRepository(session)
            run = await run_repo.get_by_job_id(job_id)
            if run is None:
                raise RuntimeError(
                    f'News collection metadata for batch job {job_id} was not found.'
                )

            step_run_id: int | None = None
            if lease_token is not None:
                step_run_id = await job_repo.begin_step(
                    job_id=job_id,
                    lease_token=lease_token,
                    step_code='COLLECT_NAVER_NEWS',
                )
                if step_run_id is None:
                    await job_repo.rollback()
                    raise BatchLeaseLostError('News collection worker lease was lost.')

            await job_repo.add_event(
                job_id=job_id,
                step_code='COLLECT_NAVER_NEWS',
                level=EventLevel.INFO.value,
                message='Incremental Naver news collection started.',
                context_json={
                    'runId': run.run_id,
                    'windowStartAt': run.window_start_at.isoformat(),
                    'windowEndAt': run.window_end_at.isoformat(),
                    'queryStartAt': run.query_start_at.isoformat(),
                    'queryEndAt': run.query_end_at.isoformat(),
                },
            )
            await job_repo.commit()

            keywords = await keyword_repo.list_active_keywords(
                provider_name=NAVER_NEWS_PROVIDER_NAME
            )
            provider = self._provider_factory()
            if not keywords:
                await self._fail_job(
                    job_repo=job_repo,
                    run_repo=run_repo,
                    job_id=job_id,
                    run_id=run.run_id,
                    error_code='NEWS_KEYWORDS_NOT_CONFIGURED',
                    error_message='No active Naver news keywords are configured.',
                    step_run_id=step_run_id,
                )
                return
            if not provider.is_configured():
                await self._fail_job(
                    job_repo=job_repo,
                    run_repo=run_repo,
                    job_id=job_id,
                    run_id=run.run_id,
                    error_code='NAVER_NOT_CONFIGURED',
                    error_message='Naver news API credentials are not configured.',
                    total_keyword_count=len(keywords),
                    step_run_id=step_run_id,
                )
                return

            fetched_count = 0
            matched_count = 0
            inserted_count = 0
            completed_keyword_count = 0
            partial_reasons: list[str] = []
            auth_failed = False

            for keyword in keywords:
                if auth_failed:
                    await run_repo.upsert_keyword_diagnostic(
                        _failed_diagnostic(
                            run_id=run.run_id,
                            keyword=keyword,
                            error_code='NAVER_AUTH_FAILED',
                        )
                    )
                    continue
                try:
                    result = await provider.collect_for_keyword(
                        keyword_record=keyword,
                        window_start_at=run.query_start_at,
                        window_end_at=run.query_end_at,
                    )
                    keyword_inserted_count = await raw_repo.insert_articles(
                        result.articles
                    )
                    await raw_repo.link_articles_to_keyword(
                        articles=result.articles,
                        keyword_id=keyword.keyword_id,
                        market_type=keyword.market_type,
                    )
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code
                    if status_code == 429 or 500 <= status_code < 600:
                        log_safe_exception(
                            LOGGER,
                            logging.WARNING,
                            (
                                'Transient Naver news request failed: '
                                f'job_id={job_id} keyword_id={keyword.keyword_id} '
                                f'status_code={status_code}.'
                            ),
                            exception=exc,
                        )
                        await run_repo.upsert_keyword_diagnostic(
                            _failed_diagnostic(
                                run_id=run.run_id,
                                keyword=keyword,
                                error_code='NAVER_TRANSIENT_FAILURE',
                            )
                        )
                        await job_repo.add_event(
                            job_id=job_id,
                            step_code='COLLECT_NAVER_NEWS',
                            level=EventLevel.WARN.value,
                            message=('Transient Naver news failure will be retried.'),
                            context_json={
                                'runId': run.run_id,
                                'keywordId': keyword.keyword_id,
                                'marketType': keyword.market_type,
                                'statusCode': status_code,
                                'errorCode': 'NAVER_TRANSIENT_FAILURE',
                            },
                        )
                        await job_repo.commit()
                        raise NaverRetryableError() from None
                    auth_failed = status_code in {401, 403}
                    error_code = (
                        'NAVER_AUTH_FAILED' if auth_failed else 'NAVER_REQUEST_FAILED'
                    )
                    log_safe_exception(
                        LOGGER,
                        logging.ERROR if auth_failed else logging.WARNING,
                        (
                            'Terminal Naver news request failed: '
                            f'job_id={job_id} keyword_id={keyword.keyword_id} '
                            f'status_code={status_code}.'
                        ),
                        exception=exc,
                    )
                    partial_reasons.append(
                        f'{keyword.market_type}/{keyword.keyword}: {error_code}'
                    )
                    await run_repo.upsert_keyword_diagnostic(
                        _failed_diagnostic(
                            run_id=run.run_id,
                            keyword=keyword,
                            error_code=error_code,
                        )
                    )
                except Exception as exc:
                    log_safe_exception(
                        LOGGER,
                        logging.WARNING,
                        (
                            'Naver news request failed: '
                            f'job_id={job_id} keyword_id={keyword.keyword_id}.'
                        ),
                        exception=exc,
                    )
                    partial_reasons.append(
                        f'{keyword.market_type}/{keyword.keyword}: NAVER_REQUEST_FAILED'
                    )
                    await run_repo.upsert_keyword_diagnostic(
                        _failed_diagnostic(
                            run_id=run.run_id,
                            keyword=keyword,
                            error_code='NAVER_REQUEST_FAILED',
                        )
                    )
                else:
                    fetched_count += result.fetched_count
                    matched_count += result.candidate_count
                    inserted_count += keyword_inserted_count
                    completed_keyword_count += 1
                    if not result.coverage_complete:
                        partial_reasons.append(
                            f'{keyword.market_type}/{keyword.keyword}: '
                            'NAVER_PAGINATION_CAP'
                        )
                    await run_repo.upsert_keyword_diagnostic(
                        NewsCollectionKeywordDiagnosticParams(
                            news_collection_run_id=run.run_id,
                            keyword_id=keyword.keyword_id,
                            provider_name=keyword.provider_name,
                            market_type=keyword.market_type,
                            keyword=keyword.keyword,
                            status='SUCCESS',
                            fetched_count=result.fetched_count,
                            matched_count=result.candidate_count,
                            inserted_count=keyword_inserted_count,
                            coverage_complete=result.coverage_complete,
                        )
                    )
                await job_repo.commit()

            coverage_complete = not partial_reasons
            await run_repo.finalize_run(
                run_id=run.run_id,
                total_keyword_count=len(keywords),
                completed_keyword_count=completed_keyword_count,
                fetched_count=fetched_count,
                matched_count=matched_count,
                inserted_count=inserted_count,
                coverage_complete=coverage_complete,
            )
            status = (
                BatchJobStatus.FAILED.value
                if auth_failed
                else (
                    BatchJobStatus.SUCCESS.value
                    if coverage_complete
                    else BatchJobStatus.PARTIAL.value
                )
            )
            partial_message = '; '.join(partial_reasons[:5]) or None
            await job_repo.add_event(
                job_id=job_id,
                step_code='COLLECT_NAVER_NEWS',
                level=(
                    EventLevel.INFO.value
                    if coverage_complete
                    else EventLevel.WARN.value
                ),
                message='Incremental Naver news collection completed.',
                context_json={
                    'runId': run.run_id,
                    'status': status,
                    'keywordCount': len(keywords),
                    'completedKeywordCount': completed_keyword_count,
                    'fetchedCount': fetched_count,
                    'matchedCount': matched_count,
                    'insertedCount': inserted_count,
                    'coverageComplete': coverage_complete,
                },
            )
            await job_repo.mark_job_completed(
                job_id=job_id,
                status=status,
                raw_news_count=inserted_count,
                partial_message=partial_message,
                error_code='NAVER_AUTH_FAILED' if auth_failed else None,
                error_message=(
                    'Naver news API authentication failed.' if auth_failed else None
                ),
                log_summary=(
                    f'Collected {inserted_count} new articles from '
                    f'{len(keywords)} keyword(s).'
                ),
            )
            if step_run_id is not None:
                await job_repo.finish_step_run(
                    step_run_id=step_run_id,
                    status=BatchStepStatus.SUCCEEDED.value,
                )
            await job_repo.commit()

    async def _fail_job(
        self,
        *,
        job_repo: BatchJobRepository,
        run_repo: NewsCollectionRunRepository,
        job_id: int,
        run_id: int,
        error_code: str,
        error_message: str,
        total_keyword_count: int = 0,
        step_run_id: int | None = None,
    ) -> None:
        await run_repo.finalize_run(
            run_id=run_id,
            total_keyword_count=total_keyword_count,
            completed_keyword_count=0,
            fetched_count=0,
            matched_count=0,
            inserted_count=0,
            coverage_complete=False,
        )
        await job_repo.add_event(
            job_id=job_id,
            step_code='COLLECT_NAVER_NEWS',
            level=EventLevel.ERROR.value,
            message='Incremental Naver news collection failed.',
            context_json={'errorCode': error_code},
        )
        await job_repo.mark_job_failed(
            job_id=job_id,
            error_code=error_code,
            error_message=error_message,
        )
        if step_run_id is not None:
            await job_repo.finish_step_run(
                step_run_id=step_run_id,
                status=BatchStepStatus.FAILED.value,
            )
        await job_repo.commit()


def _failed_diagnostic(
    *,
    run_id: int,
    keyword: NewsSearchKeywordRecord,
    error_code: str,
) -> NewsCollectionKeywordDiagnosticParams:
    return NewsCollectionKeywordDiagnosticParams(
        news_collection_run_id=run_id,
        keyword_id=keyword.keyword_id,
        provider_name=keyword.provider_name,
        market_type=keyword.market_type,
        keyword=keyword.keyword,
        status='FAILED',
        fetched_count=0,
        matched_count=0,
        inserted_count=0,
        coverage_complete=False,
        error_code=error_code,
        error_message='External provider request failed.',
    )


__all__ = ['NaverNewsCollectionOrchestrator', 'NaverRetryableError']
