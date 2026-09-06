from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.batch.diagnostics import NEWS_COVERAGE_INCOMPLETE
from app.batch.models import BatchExecutionContext
from app.batch.normalizers import (
    build_dedupe_hash,
    build_duplicate_key,
    canonicalize_link,
    excerpt_text,
    normalize_title,
    publisher_from_link,
)
from app.batch.policies.news_collection_slot import collectable_window_end
from app.batch.providers.article_content import (
    ArticleContentProvider,
    ArticleContentResult,
)
from app.batch.providers.naver_news import NAVER_NEWS_PROVIDER_NAME
from app.batch.steps.base import BatchStep, require_repository_session
from app.core.settings import Settings, get_settings
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.news_article_processed_repo import (
    NewsArticleProcessedRepository,
)
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.news_collection_run_repo import (
    NewsCollectionRunRepository,
    intervals_cover_window,
)
from app.db.repositories.projections import (
    NewsArticleProcessedCreateParams,
    NewsArticleRawProcessedMapCreateParams,
)


@dataclass(slots=True)
class _ArticleContentFetchTarget:
    raw_article: Any
    dedupe_hash: str
    duplicate_key: str
    link: str | None
    fallback_summary: str | None


class DedupeArticlesStep(BatchStep):
    step_code = 'DEDUPE_ARTICLES'
    started_message = 'Dedupe articles step started.'
    completed_message = 'Dedupe articles step completed.'

    def __init__(
        self,
        *,
        raw_repo_factory: Callable[[object], Any] | None = None,
        processed_repo_factory: Callable[[object], Any] | None = None,
        content_provider_factory: Callable[[], Any] | None = None,
        market_context_repo_factory: Callable[[object], Any] | None = None,
        collection_run_repo_factory: Callable[[object], Any] | None = None,
        settings: Settings | None = None,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        self._raw_repo_factory = raw_repo_factory or NewsArticleRawRepository
        self._processed_repo_factory = (
            processed_repo_factory or NewsArticleProcessedRepository
        )
        self._content_provider_factory = (
            content_provider_factory or ArticleContentProvider
        )
        self._market_context_repo_factory = (
            market_context_repo_factory or MarketContextRepository
        )
        self._collection_run_repo_factory = (
            collection_run_repo_factory or NewsCollectionRunRepository
        )
        self._settings = settings or get_settings()
        self._now_factory = now_factory or (lambda: datetime.now(UTC))

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped dedupe because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)

        raw_repo = self._raw_repo_factory(session)
        processed_repo = self._processed_repo_factory(session)
        content_provider = self._content_provider_factory()
        market_context_repo = self._market_context_repo_factory(session)
        collection_run_repo = self._collection_run_repo_factory(session)
        market_contexts = await market_context_repo.list_for_job(context.job_id)
        raw_article_targets: dict[tuple[int, str], Any] = {}
        raw_article_ids: set[int] = set()
        for market_context in market_contexts:
            raw_articles = await raw_repo.list_articles_by_window(
                window_start_at=market_context.news_window_start_at,
                window_end_at=market_context.news_window_end_at,
                market_type=market_context.market_type,
            )
            raw_article_targets.update(
                (
                    (article.raw_article_id, market_context.market_type),
                    article,
                )
                for article in raw_articles
            )
            raw_article_ids.update(article.raw_article_id for article in raw_articles)
            context.raw_news_count_by_market[market_context.market_type] = (
                context.raw_news_count_by_market.get(market_context.market_type, 0)
                + len(raw_articles)
            )
            # The news window ends at the instant this job started, which lands
            # mid-slot; the slot containing it cannot have been collected yet.
            # Judging coverage against the raw end made every run report an
            # incomplete ingest, hiding the real gaps this diagnostic is for.
            coverage_deadline = collectable_window_end(
                market_context.news_window_end_at,
                now=self._now_factory(),
            )
            intervals = await collection_run_repo.list_complete_intervals(
                provider_name=NAVER_NEWS_PROVIDER_NAME,
                market_type=market_context.market_type,
                window_start_at=market_context.news_window_start_at,
                window_end_at=coverage_deadline,
            )
            coverage_complete = intervals_cover_window(
                intervals,
                window_start_at=market_context.news_window_start_at,
                window_end_at=coverage_deadline,
            )
            await market_context_repo.set_news_coverage_complete(
                job_id=context.job_id,
                market_type=market_context.market_type,
                coverage_complete=coverage_complete,
            )
            if not coverage_complete:
                context.add_partial(
                    NEWS_COVERAGE_INCOMPLETE,
                    f'{market_context.market_type} news ingestion coverage '
                    'is incomplete.',
                )
        raw_articles = list(raw_article_targets.values())
        context.raw_news_count = len(raw_article_ids)
        if not raw_articles:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='No raw articles found for deduplication.',
            )
            context.log_messages.append(
                'No raw articles were available for deduplication.'
            )
            return context

        unique_targets: list[_ArticleContentFetchTarget] = []
        seen_duplicate_keys: set[str] = set()
        for raw_article in raw_articles:
            link = raw_article.origin_link or raw_article.naver_link
            duplicate_key = build_duplicate_key(
                raw_article.title,
                raw_article.publisher_name or publisher_from_link(link),
            )
            # Fetching once per duplicate group rather than once per URL also
            # spares the content provider every redundant request.
            if duplicate_key in seen_duplicate_keys:
                continue
            description = None
            if isinstance(raw_article.payload_json, dict):
                description = raw_article.payload_json.get('description')
            unique_targets.append(
                _ArticleContentFetchTarget(
                    raw_article=raw_article,
                    dedupe_hash=build_dedupe_hash(raw_article.title, link),
                    duplicate_key=duplicate_key,
                    link=link,
                    fallback_summary=excerpt_text(description),
                )
            )
            seen_duplicate_keys.add(duplicate_key)

        content_results = await self._fetch_unique_article_contents(
            content_provider=content_provider,
            targets=unique_targets,
        )
        content_by_key = {
            target.duplicate_key: result
            for target, result in zip(unique_targets, content_results, strict=True)
        }

        seen_duplicate_groups: dict[tuple[str, str], int] = {}
        processed_ids: set[int] = set()
        processed_ids_by_market: dict[str, set[int]] = {}

        for raw_article in raw_articles:
            link = raw_article.origin_link or raw_article.naver_link
            publisher_name = raw_article.publisher_name or publisher_from_link(link)
            dedupe_hash = build_dedupe_hash(raw_article.title, link)
            duplicate_key = build_duplicate_key(raw_article.title, publisher_name)
            market_duplicate_key = (raw_article.market_type, duplicate_key)
            processed_id = seen_duplicate_groups.get(market_duplicate_key)
            if processed_id is None:
                content_result = content_by_key[duplicate_key]
                if content_result.failure_details:
                    await repository.add_event(
                        job_id=context.job_id,
                        step_code=self.step_code,
                        level=EventLevel.WARN.value,
                        message='Article content fetch recorded provider failure.',
                        context_json={
                            'rawArticleId': raw_article.raw_article_id,
                            'dedupeHash': dedupe_hash,
                            'fallbackUsed': content_result.fallback_used,
                            'failures': content_result.failure_details,
                        },
                    )
                processed = await processed_repo.get_or_create_processed_article(
                    NewsArticleProcessedCreateParams(
                        business_date=context.business_date,
                        market_type=raw_article.market_type,
                        dedupe_hash=dedupe_hash,
                        canonical_title=normalize_title(raw_article.title),
                        # Raw rows collected before publishers were extracted
                        # still carry None, so derive rather than propagate it.
                        publisher_name=publisher_name,
                        published_at=raw_article.published_at,
                        origin_link=canonicalize_link(link),
                        naver_link=raw_article.naver_link,
                        source_summary=content_result.source_summary,
                        article_body_excerpt=content_result.body_excerpt,
                        content_json={
                            'providerName': raw_article.provider_name,
                            'providerArticleKey': raw_article.provider_article_key,
                            'payload': raw_article.payload_json,
                            'dedupeHash': dedupe_hash,
                            'bodyText': content_result.body_text,
                            'sourceDomain': content_result.source_domain,
                            'fetchedUrl': content_result.fetched_url,
                            'contentFallbackUsed': content_result.fallback_used,
                        },
                    )
                )
                processed_id = processed.processed_article_id
                seen_duplicate_groups[market_duplicate_key] = processed_id
            await processed_repo.link_raw_to_processed(
                NewsArticleRawProcessedMapCreateParams(
                    raw_article_id=raw_article.raw_article_id,
                    processed_article_id=processed_id,
                )
            )
            processed_ids.add(processed_id)
            processed_ids_by_market.setdefault(raw_article.market_type, set()).add(
                processed_id
            )

        context.processed_news_count += len(processed_ids)
        for market_type, market_processed_ids in processed_ids_by_market.items():
            context.processed_news_count_by_market[market_type] = (
                context.processed_news_count_by_market.get(market_type, 0)
                + len(market_processed_ids)
            )
        context.log_messages.append(
            f'Deduplicated {len(raw_article_ids)} raw articles into '
            f'{len(processed_ids)} processed articles.'
        )
        await repository.add_event(
            job_id=context.job_id,
            step_code=self.step_code,
            level=EventLevel.INFO.value,
            message='Article deduplication completed.',
            context_json={
                'rawArticleCount': len(raw_article_ids),
                'processedArticleCount': len(processed_ids),
            },
        )
        return context

    async def _fetch_unique_article_contents(
        self,
        *,
        content_provider: Any,
        targets: list[_ArticleContentFetchTarget],
    ) -> list[ArticleContentResult]:
        semaphore = asyncio.Semaphore(self._settings.article_crawl_concurrency_limit)

        async def fetch(target: _ArticleContentFetchTarget) -> ArticleContentResult:
            try:
                async with semaphore:
                    return await content_provider.fetch_article_content(
                        origin_link=target.raw_article.origin_link,
                        naver_link=target.raw_article.naver_link,
                        fallback_summary=target.fallback_summary,
                    )
            except Exception as exc:
                return ArticleContentResult(
                    body_text=target.fallback_summary,
                    body_excerpt=target.fallback_summary,
                    source_summary=target.fallback_summary,
                    source_domain=None,
                    fetched_url=target.link,
                    fallback_used=True,
                    failure_details=[
                        {
                            'provider': 'ArticleContentProvider',
                            'url': target.link or '',
                            'error_class': type(exc).__name__,
                            'error_message': str(exc),
                        }
                    ],
                )

        return await asyncio.gather(*(fetch(target) for target in targets))


__all__ = ['DedupeArticlesStep']
