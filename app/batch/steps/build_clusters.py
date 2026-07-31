from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

from app.batch.logging import log_safe_exception
from app.batch.models import BatchExecutionContext
from app.batch.normalizers import normalize_title, tokenize_text
from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.target_progress import (
    DurableTargetProgress,
    TargetCall,
    run_target_calls,
)
from app.core.llm import LlmRetryableError
from app.core.public_diagnostics import (
    public_ai_invalid_response,
    public_ai_provider_error,
)
from app.core.settings import Settings, get_settings
from app.db.enums import EventLevel
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.news_article_processed_repo import (
    NewsArticleProcessedRepository,
)
from app.db.repositories.news_cluster_write_repo import NewsClusterWriteRepository
from app.db.repositories.projections import NewsClusterCreateParams

LOGGER = logging.getLogger(__name__)


def _serialize_exception(exc: Exception) -> dict[str, str]:
    return public_ai_provider_error(exc)


def _serialize_malformed_response(reason: str) -> dict[str, str]:
    _ = reason
    return public_ai_invalid_response()


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
        settings: Settings | None = None,
    ) -> None:
        self._processed_repo_factory = (
            processed_repo_factory or NewsArticleProcessedRepository
        )
        self._cluster_repo_factory = cluster_repo_factory or NewsClusterWriteRepository
        self._llm_provider_factory = llm_provider_factory or BatchLlmProvider
        self._max_clusters_per_market = (
            settings or get_settings()
        ).batch_max_clusters_per_market

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
        progress = await DurableTargetProgress.load(
            repository,
            job_id=context.job_id,
            step_code=self.step_code,
        )

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

        total_selected_count = 0
        for market_type in sorted(grouped_articles):
            articles = grouped_articles[market_type]
            candidate_clusters = _rank_market_clusters(_group_articles(articles))
            selected_clusters = candidate_clusters[: self._max_clusters_per_market]
            candidate_count = len(candidate_clusters)
            selected_count = len(selected_clusters)
            total_selected_count += selected_count
            omitted_count = candidate_count - selected_count
            selection_context = {
                'marketType': market_type,
                'candidateCount': candidate_count,
                'selectedCount': selected_count,
                'omittedCount': omitted_count,
                'maxClustersPerMarket': self._max_clusters_per_market,
            }
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.INFO.value,
                message='Selected cluster candidates for persistence.',
                context_json=selection_context,
            )
            LOGGER.info(
                (
                    'cluster_selection market_type=%s candidate_count=%s '
                    'selected_count=%s omitted_count=%s max_clusters_per_market=%s'
                ),
                market_type,
                candidate_count,
                selected_count,
                omitted_count,
                self._max_clusters_per_market,
                extra={
                    'batch_market_type': market_type,
                    'batch_cluster_candidate_count': candidate_count,
                    'batch_cluster_selected_count': selected_count,
                    'batch_cluster_omitted_count': omitted_count,
                    'batch_max_clusters_per_market': (self._max_clusters_per_market),
                },
            )
            context.log_messages.append(
                f'{market_type} cluster candidates: candidate={candidate_count}, '
                f'selected={selected_count}, omitted={omitted_count}.'
            )
            market_target_prefix = f'{market_type}:'
            completed_market_targets = {
                target_key
                for target_key in progress.completed_targets
                if target_key.startswith(market_target_prefix)
            }
            if (
                not completed_market_targets
                and hasattr(cluster_repo, 'list_cluster_ids_for_business_date')
                and hasattr(cluster_repo, 'delete_clusters_by_ids')
            ):
                existing_cluster_ids = (
                    await cluster_repo.list_cluster_ids_for_business_date(
                        context.business_date,
                        market_type,
                    )
                )
                await cluster_repo.delete_clusters_by_ids(existing_cluster_ids)

            concurrency_limit = getattr(llm_provider, 'concurrency_limit', 1)
            semaphore = asyncio.Semaphore(concurrency_limit)

            async def bounded_enrich(
                ordered_articles: list,
                *,
                target_semaphore: asyncio.Semaphore = semaphore,
                target_market_type: str = market_type,
            ) -> dict:
                async with target_semaphore:
                    return await _enrich_cluster(
                        llm_provider,
                        target_market_type,
                        ordered_articles,
                    )

            articles_by_target = {
                f'{market_type}:{cluster_rank}': (cluster_rank, ordered_articles)
                for cluster_rank, ordered_articles in enumerate(
                    selected_clusters,
                    start=1,
                )
            }

            async def persist_enrichment(
                target_key: str,
                enrichment: dict[str, Any],
                *,
                target_articles: dict[str, tuple[int, list]] = articles_by_target,
                target_market_type: str = market_type,
            ) -> None:
                cluster_rank, ordered_articles = target_articles[target_key]
                if enrichment.get('fallback_used'):
                    context.fallback_count += 1
                    error_context = enrichment.get('error_context')
                    diagnostic = (
                        error_context.get('message')
                        if isinstance(error_context, dict)
                        else 'LLM provider is not configured.'
                    )
                    partial_reason = (
                        f'Cluster enrichment fallback for {target_market_type} '
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
                            'marketType': target_market_type,
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
                        market_type=target_market_type,
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
                context.cluster_count += 1
                await repository.add_event(
                    job_id=context.job_id,
                    step_code=self.step_code,
                    level=EventLevel.INFO.value,
                    message='Created clustering bundle.',
                    context_json={
                        'marketType': target_market_type,
                        'clusterId': cluster.cluster_id,
                        'clusterRank': cluster_rank,
                        'articleCount': len(ordered_articles),
                    },
                )
                await progress.commit_target(target_key, context)

            pending_calls = [
                TargetCall(
                    target_key=target_key,
                    invoke=partial(bounded_enrich, ordered_articles),
                )
                for target_key, (_rank, ordered_articles) in articles_by_target.items()
                if target_key not in progress.completed_targets
            ]
            await run_target_calls(pending_calls, on_result=persist_enrichment)

        context.cluster_count = total_selected_count
        context.log_messages.append(
            f'Created {context.cluster_count} clustering scaffold bundle(s) '
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
    group_tokens: list[set[str]] = []
    for article in sorted(
        articles,
        key=lambda candidate: candidate.processed_article_id,
    ):
        article_tokens = set(tokenize_text(article.canonical_title))
        matched_index: int | None = None
        for group_index, tokens in enumerate(group_tokens):
            if article_tokens and len(article_tokens.intersection(tokens)) >= 2:
                matched_index = group_index
                break
        if matched_index is None:
            groups.append([article])
            group_tokens.append(set(article_tokens))
        else:
            groups[matched_index].append(article)
            group_tokens[matched_index].update(article_tokens)
    return groups


def _rank_market_clusters(clusters: list[list]) -> list[list]:
    ordered_clusters = [
        sorted(
            cluster_articles,
            key=lambda article: (
                article.published_at or datetime.min.replace(tzinfo=UTC),
                article.processed_article_id,
            ),
            reverse=True,
        )
        for cluster_articles in clusters
    ]
    ordered_clusters.sort(
        key=lambda cluster_articles: cluster_articles[0].processed_article_id
    )
    ordered_clusters.sort(
        key=lambda cluster_articles: (
            cluster_articles[0].published_at or datetime.min.replace(tzinfo=UTC)
        ),
        reverse=True,
    )
    ordered_clusters.sort(key=len, reverse=True)
    return ordered_clusters


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
    except LlmRetryableError:
        raise
    except Exception as exc:
        log_safe_exception(
            LOGGER,
            logging.WARNING,
            'Cluster enrichment provider request failed.',
            exception=exc,
        )
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
    except TypeError, ValueError:
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
        'analysis_paragraphs': analysis_paragraphs or fallback['analysis_paragraphs'],
        'representative_article_id': articles[
            representative_index
        ].processed_article_id,
        'fallback_used': False,
        'fallback_reason': 'llm',
        'error_context': None,
    }


__all__ = ['BuildClustersStep']
