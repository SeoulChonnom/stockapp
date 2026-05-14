from __future__ import annotations

from app.batch.models import BatchExecutionContext
from app.batch.normalizers import (
    build_dedupe_hash,
    canonicalize_link,
    excerpt_text,
    normalize_title,
)
from app.batch.providers.article_content import ArticleContentProvider
from app.batch.steps.base import BatchStep
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_article_processed_repo import (
    NewsArticleProcessedRepository,
)
from app.db.repositories.news_article_raw_repo import NewsArticleRawRepository
from app.db.repositories.projections import (
    NewsArticleProcessedCreateParams,
    NewsArticleRawProcessedMapCreateParams,
)


class DedupeArticlesStep(BatchStep):
    step_code = 'DEDUPE_ARTICLES'
    started_message = 'Dedupe articles step started.'
    completed_message = 'Dedupe articles step completed.'

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

        session = getattr(repository, 'session', None)
        if session is None:
            context.log_messages.append('Article deduplication step is scaffolded.')
            return context

        raw_repo = NewsArticleRawRepository(session)
        processed_repo = NewsArticleProcessedRepository(session)
        content_provider = ArticleContentProvider()

        raw_articles = await raw_repo.list_articles_by_business_date(
            context.business_date
        )
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

        seen_hashes: dict[str, int] = {}
        processed_ids: set[int] = set()

        for raw_article in raw_articles:
            link = raw_article.origin_link or raw_article.naver_link
            dedupe_hash = build_dedupe_hash(raw_article.title, link)
            processed_id = seen_hashes.get(dedupe_hash)
            if processed_id is None:
                description = None
                if isinstance(raw_article.payload_json, dict):
                    description = raw_article.payload_json.get('description')
                content_result = await content_provider.fetch_article_content(
                    origin_link=raw_article.origin_link,
                    naver_link=raw_article.naver_link,
                    fallback_summary=excerpt_text(description),
                )
                processed = await processed_repo.get_or_create_processed_article(
                    NewsArticleProcessedCreateParams(
                        business_date=raw_article.business_date,
                        market_type=raw_article.market_type,
                        dedupe_hash=dedupe_hash,
                        canonical_title=normalize_title(raw_article.title),
                        publisher_name=raw_article.publisher_name,
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
                seen_hashes[dedupe_hash] = processed_id
            await processed_repo.link_raw_to_processed(
                NewsArticleRawProcessedMapCreateParams(
                    raw_article_id=raw_article.raw_article_id,
                    processed_article_id=processed_id,
                )
            )
            processed_ids.add(processed_id)

        context.processed_news_count += len(processed_ids)
        context.log_messages.append(
            f'Deduplicated {len(raw_articles)} raw articles into '
            f'{len(processed_ids)} processed articles.'
        )
        await repository.add_event(
            job_id=context.job_id,
            step_code=self.step_code,
            level=EventLevel.INFO.value,
            message='Article deduplication completed.',
            context_json={
                'rawArticleCount': len(raw_articles),
                'processedArticleCount': len(processed_ids),
            },
        )
        await session.commit()
        return context


__all__ = ['DedupeArticlesStep']
