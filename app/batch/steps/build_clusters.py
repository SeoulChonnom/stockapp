from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Callable
from functools import partial
from typing import Any

from app.batch.diagnostics import CLUSTER_ENRICHMENT_FALLBACK
from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.cluster_enrichment import (
    _enrich_cluster,
    _group_articles,
    _rank_market_clusters,
)
from app.batch.steps.target_progress import (
    DurableTargetProgress,
    TargetCall,
    run_target_calls,
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
        resolved_settings = settings or get_settings()
        self._max_clusters_per_market = resolved_settings.batch_max_clusters_per_market
        self._processed_article_limit = (
            resolved_settings.batch_clustering_processed_article_limit
        )
        self._max_articles_per_cluster = (
            resolved_settings.batch_max_articles_per_cluster
        )

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
            context.business_date,
            limit=self._processed_article_limit,
        )
        await _warn_if_market_limit_dropped_articles(
            processed_repo=processed_repo,
            repository=repository,
            context=context,
            step_code=self.step_code,
            processed_articles=processed_articles,
            limit=self._processed_article_limit,
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
            grouped = await asyncio.to_thread(
                _group_articles,
                articles,
                max_articles_per_group=self._max_articles_per_cluster,
            )
            candidate_clusters = _rank_market_clusters(grouped)
            selected_clusters = candidate_clusters[: self._max_clusters_per_market]
            total_selected_count += len(selected_clusters)

            await _log_cluster_selection(
                repository=repository,
                context=context,
                step_code=self.step_code,
                market_type=market_type,
                candidate_clusters=candidate_clusters,
                selected_clusters=selected_clusters,
                max_clusters_per_market=self._max_clusters_per_market,
            )
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

            persist_enrichment = partial(
                _persist_cluster_enrichment,
                context=context,
                repository=repository,
                step_code=self.step_code,
                cluster_repo=cluster_repo,
                progress=progress,
                articles_by_target=articles_by_target,
                market_type=market_type,
            )

            pending_calls = [
                TargetCall(
                    target_key=target_key,
                    invoke=partial(bounded_enrich, ordered_articles),
                )
                for target_key, (_rank, ordered_articles) in articles_by_target.items()
                if target_key not in progress.completed_targets
            ]
            await run_target_calls(pending_calls, on_result=persist_enrichment)
            await _delete_stale_market_clusters(
                cluster_repo=cluster_repo,
                business_date=context.business_date,
                market_type=market_type,
                keep_max_rank=len(selected_clusters),
            )

        context.cluster_count = total_selected_count
        context.log_messages.append(
            f'Created {context.cluster_count} clustering scaffold bundle(s) '
            f'from {len(processed_articles)} processed articles.'
        )
        return context


async def _warn_if_market_limit_dropped_articles(
    *,
    processed_repo: Any,
    repository: BatchJobRepository,
    context: BatchExecutionContext,
    step_code: str,
    processed_articles: list,
    limit: int,
) -> None:
    """Warn per market when the clustering query LIMIT dropped articles.

    The LIMIT is applied per market_type partition (see
    NewsArticleProcessedRepository.list_processed_by_business_date), so a
    single combined count would hide that one market lost rows while the
    other did not. The true per-market totals live only in the database --
    `processed_articles` already reflects whatever the LIMIT let through --
    so a market's omission count is only knowable via a fresh count query.
    """
    selected_counts: dict[str, int] = defaultdict(int)
    for article in processed_articles:
        selected_counts[article.market_type] += 1

    market_totals = await processed_repo.count_processed_by_business_date(
        context.business_date
    )
    for market_type, total_count in sorted(market_totals.items()):
        selected_count = selected_counts.get(market_type, 0)
        omitted_count = total_count - selected_count
        if omitted_count <= 0:
            continue
        await repository.add_event(
            job_id=context.job_id,
            step_code=step_code,
            level=EventLevel.WARN.value,
            message=(
                'Processed article count reached the clustering query '
                'limit; some articles may be excluded from clustering.'
            ),
            context_json={
                'marketType': market_type,
                'limit': limit,
                'selectedCount': selected_count,
                'omittedCount': omitted_count,
            },
        )


async def _log_cluster_selection(
    *,
    repository: BatchJobRepository,
    context: BatchExecutionContext,
    step_code: str,
    market_type: str,
    candidate_clusters: list[list],
    selected_clusters: list[list],
    max_clusters_per_market: int,
) -> None:
    candidate_count = len(candidate_clusters)
    selected_count = len(selected_clusters)
    omitted_count = candidate_count - selected_count
    selection_context = {
        'marketType': market_type,
        'candidateCount': candidate_count,
        'selectedCount': selected_count,
        'omittedCount': omitted_count,
        'maxClustersPerMarket': max_clusters_per_market,
    }
    await repository.add_event(
        job_id=context.job_id,
        step_code=step_code,
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
        max_clusters_per_market,
        extra={
            'batch_market_type': market_type,
            'batch_cluster_candidate_count': candidate_count,
            'batch_cluster_selected_count': selected_count,
            'batch_cluster_omitted_count': omitted_count,
            'batch_max_clusters_per_market': max_clusters_per_market,
        },
    )
    context.log_messages.append(
        f'{market_type} cluster candidates: candidate={candidate_count}, '
        f'selected={selected_count}, omitted={omitted_count}.'
    )


async def _delete_stale_market_clusters(
    *,
    cluster_repo: Any,
    business_date: Any,
    market_type: str,
    keep_max_rank: int,
) -> None:
    """Remove leftover clusters from a prior run that this run no longer produces.

    New/updated ranks are persisted in place via ``create_cluster_bundle``'s
    upsert, which preserves ``news_cluster.id`` (and therefore existing
    ``ai_summary``/``market_daily_page_market_cluster`` references) for
    ranks that survive across runs. Only ranks beyond what this run selected
    (e.g. the market now has fewer clusters than a previous run) are stale
    and safe to remove once this market's new clusters have been persisted.
    """
    if not (
        hasattr(cluster_repo, 'list_cluster_ids_for_business_date')
        and hasattr(cluster_repo, 'delete_clusters_by_ids')
    ):
        return
    stale_cluster_ids = await cluster_repo.list_cluster_ids_for_business_date(
        business_date,
        market_type,
        min_rank=keep_max_rank,
    )
    await cluster_repo.delete_clusters_by_ids(stale_cluster_ids)


async def _persist_cluster_enrichment(
    target_key: str,
    enrichment: dict[str, Any],
    *,
    context: BatchExecutionContext,
    repository: BatchJobRepository,
    step_code: str,
    cluster_repo: Any,
    progress: DurableTargetProgress,
    articles_by_target: dict[str, tuple[int, list]],
    market_type: str,
) -> None:
    cluster_rank, ordered_articles = articles_by_target[target_key]
    if enrichment.get('fallback_used'):
        context.fallback_count += 1
        error_context = enrichment.get('error_context')
        diagnostic = (
            error_context.get('message')
            if isinstance(error_context, dict)
            else 'LLM provider is not configured.'
        )
        context.add_partial(
            CLUSTER_ENRICHMENT_FALLBACK,
            f'Cluster enrichment fallback for {market_type} '
            f'cluster {cluster_rank}: {diagnostic}',
        )
        await repository.add_event(
            job_id=context.job_id,
            step_code=step_code,
            level=EventLevel.WARN.value,
            message='Cluster enrichment used fallback response.',
            context_json={
                'marketType': market_type,
                'clusterRank': cluster_rank,
                'representativeArticleId': enrichment['representative_article_id'],
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
            representative_article_id=enrichment['representative_article_id'],
            article_count=len(ordered_articles),
        ),
        [article.processed_article_id for article in ordered_articles],
    )
    context.cluster_count += 1
    await repository.add_event(
        job_id=context.job_id,
        step_code=step_code,
        level=EventLevel.INFO.value,
        message='Created clustering bundle.',
        context_json={
            'marketType': market_type,
            'clusterId': cluster.cluster_id,
            'clusterRank': cluster_rank,
            'articleCount': len(ordered_articles),
        },
    )
    await progress.commit_target(target_key, context)


__all__ = [
    'BuildClustersStep',
]
