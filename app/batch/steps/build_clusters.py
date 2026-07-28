from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.batch.models import BatchExecutionContext
from app.batch.normalizers import normalize_title, tokenize_text
from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.base import BatchStep, require_repository_session
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_article_processed_repo import (
    NewsArticleProcessedRepository,
)
from app.db.repositories.news_cluster_write_repo import NewsClusterWriteRepository
from app.db.repositories.projections import NewsClusterCreateParams


def _serialize_exception(exc: Exception) -> dict[str, str]:
    return {
        'provider': 'BatchLlmProvider',
        'errorClass': type(exc).__name__,
        'errorMessage': str(exc),
    }


def _serialize_malformed_response(reason: str) -> dict[str, str]:
    return {
        'provider': 'BatchLlmProvider',
        'errorClass': 'ValueError',
        'errorMessage': reason,
    }


class BuildClustersStep(BatchStep):
    step_code = 'BUILD_CLUSTERS'
    started_message = 'Build clusters step started.'
    completed_message = 'Build clusters step completed.'

    def __init__(
        self,
        *,
        processed_repo_factory: Callable[[object], Any] | None = None,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        llm_provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._processed_repo_factory = (
            processed_repo_factory or NewsArticleProcessedRepository
        )
        self._cluster_repo_factory = cluster_repo_factory or NewsClusterWriteRepository
        self._llm_provider_factory = llm_provider_factory or BatchLlmProvider

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped cluster building because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)

        processed_repo = self._processed_repo_factory(session)
        cluster_repo = self._cluster_repo_factory(session)
        llm_provider = self._llm_provider_factory()

        processed_articles = await processed_repo.list_by_business_date(
            context.business_date
        )
        if not processed_articles:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='No processed articles found for clustering.',
            )
            context.log_messages.append(
                'No processed articles were available for clustering.'
            )
            return context

        grouped_articles: dict[str, list] = defaultdict(list)
        for article in processed_articles:
            grouped_articles[article.market_type].append(article)

        created_cluster_count = 0
        for market_type, articles in grouped_articles.items():
            market_clusters = (
                [articles]
                if not llm_provider.is_configured()
                else _group_articles(articles)
            )
            if hasattr(cluster_repo, 'list_cluster_ids_for_business_date') and hasattr(
                cluster_repo, 'delete_clusters_by_ids'
            ):
                existing_cluster_ids = (
                    await cluster_repo.list_cluster_ids_for_business_date(
                        context.business_date,
                        market_type,
                    )
                )
                await cluster_repo.delete_clusters_by_ids(existing_cluster_ids)
            ordered_cluster_articles = [
                sorted(
                    cluster_articles,
                    key=lambda article: (
                        article.published_at or datetime.min.replace(tzinfo=UTC),
                        article.processed_article_id,
                    ),
                    reverse=True,
                )
                for cluster_articles in market_clusters
            ]
            enrichments = await _enrich_market_clusters(
                llm_provider,
                market_type,
                ordered_cluster_articles,
            )
            for cluster_rank, (ordered_articles, enrichment) in enumerate(
                zip(ordered_cluster_articles, enrichments, strict=True), start=1
            ):
                if enrichment.get('fallback_used'):
                    context.fallback_count += 1
                    error_context = enrichment.get('error_context')
                    diagnostic = (
                        error_context.get('errorMessage')
                        if isinstance(error_context, dict)
                        else 'LLM provider is not configured.'
                    )
                    partial_reason = (
                        f'Cluster enrichment fallback for {market_type} '
                        f'cluster {cluster_rank}: {diagnostic}'
                    )
                    if partial_reason not in context.partial_reasons:
                        context.partial_reasons.append(partial_reason)
                    await repository.add_event(
                        job_id=context.job_id,
                        step_code=self.step_code,
                        level=EventLevel.WARN.value,
                        message='Cluster enrichment used fallback response.',
                        context_json={
                            'marketType': market_type,
                            'clusterRank': cluster_rank,
                            'representativeArticleId': enrichment[
                                'representative_article_id'
                            ],
                            'fallbackReason': enrichment.get('fallback_reason'),
                            'error': error_context,
                        },
                    )
                cluster = await cluster_repo.create_cluster_bundle(
                    NewsClusterCreateParams(
                        business_date=context.business_date,
                        market_type=market_type,
                        cluster_rank=cluster_rank,
                        title=enrichment['title'],
                        summary_short=enrichment['summary_short'],
                        summary_long=enrichment['summary_long'],
                        analysis_paragraphs_json=enrichment['analysis_paragraphs'],
                        tags_json=enrichment['tags'],
                        representative_article_id=enrichment[
                            'representative_article_id'
                        ],
                        article_count=len(ordered_articles),
                    ),
                    [article.processed_article_id for article in ordered_articles],
                )
                created_cluster_count += 1
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.INFO.value,
                    message='Created clustering bundle.',
                    context_json={
                        'marketType': market_type,
                        'clusterId': cluster.cluster_id,
                        'clusterRank': cluster_rank,
                        'articleCount': len(ordered_articles),
                    },
                )
        context.cluster_count += created_cluster_count
        context.log_messages.append(
            f'Created {created_cluster_count} clustering scaffold bundle(s) '
            f'from {len(processed_articles)} processed articles.'
        )
        return context


def _derive_tags(titles: list[str]) -> list[str]:
    tokens: list[str] = []
    for title in titles:
        for token in title.replace('/', ' ').replace('|', ' ').split():
            cleaned = token.strip()
            if len(cleaned) < 2:
                continue
            if cleaned not in tokens:
                tokens.append(cleaned)
            if len(tokens) >= 5:
                return tokens
    return tokens


def _group_articles(articles: list) -> list[list]:
    groups: list[list] = []
    for article in articles:
        article_tokens = set(tokenize_text(article.canonical_title))
        matched_group: list | None = None
        for group in groups:
            group_tokens = set()
            for group_article in group:
                group_tokens.update(tokenize_text(group_article.canonical_title))
            if article_tokens and len(article_tokens.intersection(group_tokens)) >= 2:
                matched_group = group
                break
        if matched_group is None:
            groups.append([article])
        else:
            matched_group.append(article)
    return groups


async def _enrich_cluster(
    llm_provider: BatchLlmProvider, market_type: str, articles: list
) -> dict:
    representative = articles[0]
    payload = [
        {
            'processedArticleId': article.processed_article_id,
            'title': article.canonical_title,
            'publisherName': article.publisher_name,
            'publishedAt': article.published_at.isoformat()
            if article.published_at
            else None,
            'summary': article.source_summary,
            'excerpt': article.article_body_excerpt,
        }
        for article in articles
    ]
    fallback = {
        'title': normalize_title(representative.canonical_title),
        'summary_short': representative.source_summary
        or representative.article_body_excerpt,
        'summary_long': ' / '.join(
            [article.source_summary for article in articles if article.source_summary][
                :3
            ]
        )
        or representative.article_body_excerpt,
        'tags': _derive_tags([article.canonical_title for article in articles]),
        'analysis_paragraphs': [
            value
            for value in [
                article.source_summary or article.article_body_excerpt
                for article in articles[:3]
            ]
            if value
        ],
        'representative_article_id': representative.processed_article_id,
        'fallback_used': True,
        'fallback_reason': 'llm_fallback',
        'error_context': None,
    }
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.enrich_cluster(
            market_type=market_type, articles=payload
        )
    except Exception as exc:
        fallback['error_context'] = _serialize_exception(exc)
        return fallback

    if not isinstance(result, dict):
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = _serialize_malformed_response(
            'Cluster enrichment response must be an object.'
        )
        return fallback

    tags = result.get('tags')
    if tags is not None and not isinstance(tags, list):
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = _serialize_malformed_response(
            'Cluster enrichment tags must be a list.'
        )
        return fallback
    analysis_paragraphs = result.get('analysis_paragraphs')
    if analysis_paragraphs is not None and not isinstance(analysis_paragraphs, list):
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = _serialize_malformed_response(
            'Cluster enrichment analysis_paragraphs must be a list.'
        )
        return fallback
    try:
        representative_index = int(result.get('representative_article_index', 0) or 0)
    except (TypeError, ValueError):
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = _serialize_malformed_response(
            'Cluster enrichment representative_article_index must be an integer.'
        )
        return fallback
    if representative_index < 0 or representative_index >= len(articles):
        representative_index = 0
    return {
        'title': result.get('title') or fallback['title'],
        'summary_short': result.get('summary_short') or fallback['summary_short'],
        'summary_long': result.get('summary_long') or fallback['summary_long'],
        'tags': tags or fallback['tags'],
        'analysis_paragraphs': analysis_paragraphs
        or fallback['analysis_paragraphs'],
        'representative_article_id': articles[
            representative_index
        ].processed_article_id,
        'fallback_used': False,
        'fallback_reason': 'llm',
        'error_context': None,
    }


async def _enrich_market_clusters(
    llm_provider: BatchLlmProvider, market_type: str, clusters: list[list]
) -> list[dict]:
    concurrency_limit = getattr(llm_provider, 'concurrency_limit', 1)
    semaphore = asyncio.Semaphore(concurrency_limit)

    async def enrich(articles: list) -> dict:
        async with semaphore:
            return await _enrich_cluster(llm_provider, market_type, articles)

    return await asyncio.gather(*(enrich(articles) for articles in clusters))


__all__ = ['BuildClustersStep']
