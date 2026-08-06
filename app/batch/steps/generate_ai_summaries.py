from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any, TypedDict, cast

from app.batch.ai_summary_targets import build_ai_summary_target_key
from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import PROMPT_VERSION, BatchLlmProvider
from app.batch.steps.ai_summary_generators import (
    _generate_cluster_card_summary,
    _generate_cluster_detail_summary,
    _generate_global_headline,
    _generate_market_summary,
)
from app.batch.steps.base import BatchStep, require_repository_session
from app.batch.steps.target_progress import (
    DurableTargetProgress,
    TargetCall,
    run_target_calls,
)
from app.db.enums import AiSummaryStatus, AiSummaryType, EventLevel
from app.db.repositories.ai_summary_write_repo import AiSummaryWriteRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import AiSummaryCreateParams


class _SummaryJob(TypedDict):
    summary_type: str
    market_type: str | None
    cluster_id: int | None
    target_key: str
    generate: Callable[[], Awaitable[dict[str, Any]]]


class GenerateAiSummariesStep(BatchStep):
    step_code = 'GENERATE_AI_SUMMARIES'
    started_message = 'Generate AI summaries step started.'
    completed_message = 'Generate AI summaries step completed.'

    def __init__(
        self,
        *,
        cluster_repo_factory: Callable[[object], Any] | None = None,
        index_repo_factory: Callable[[object], Any] | None = None,
        summary_repo_factory: Callable[[object], Any] | None = None,
        llm_provider_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._cluster_repo_factory = cluster_repo_factory or ClusterRepository
        self._index_repo_factory = index_repo_factory or MarketIndexRepository
        self._summary_repo_factory = summary_repo_factory or AiSummaryWriteRepository
        self._llm_provider_factory = llm_provider_factory or BatchLlmProvider

    async def run(
        self,
        repository: BatchJobRepository,
        context: BatchExecutionContext,
    ) -> BatchExecutionContext:
        if context.rebuild_page_only:
            context.log_messages.append(
                'Skipped AI summary generation because rebuild_page_only=true.'
            )
            return context

        session = require_repository_session(repository, step_code=self.step_code)

        cluster_repo = self._cluster_repo_factory(session)
        index_repo = self._index_repo_factory(session)
        summary_repo = self._summary_repo_factory(session)
        llm_provider = self._llm_provider_factory()

        clusters = await cluster_repo.list_clusters_by_business_date(
            context.business_date
        )
        indices = await index_repo.list_indices_by_business_date(context.business_date)
        if not clusters:
            reason = '요약 생성에 필요한 클러스터가 없습니다.'
            context.partial_reasons.append(reason)
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='Skipped AI summary generation because no clusters exist.',
                context_json={'businessDate': context.business_date.isoformat()},
            )
            return context

        concurrency_limit = getattr(llm_provider, 'concurrency_limit', 1)
        semaphore = asyncio.Semaphore(concurrency_limit)
        progress = await DurableTargetProgress.load(
            repository,
            job_id=context.job_id,
            step_code=self.step_code,
        )

        async def bounded_generate(callable_, *args, **kwargs) -> dict:
            async with semaphore:
                return await callable_(*args, **kwargs)

        summary_jobs = await _build_summary_jobs(
            cluster_repo=cluster_repo,
            llm_provider=llm_provider,
            clusters=clusters,
            indices=indices,
            bounded_generate=bounded_generate,
        )

        context.ai_target_count = len(summary_jobs)
        summary_jobs_by_key = {
            summary_job['target_key']: summary_job for summary_job in summary_jobs
        }
        fallback_details: list[dict[str, Any]] = []

        persist_result = partial(
            _persist_summary_result,
            context=context,
            repository=repository,
            step_code=self.step_code,
            summary_repo=summary_repo,
            summary_jobs_by_key=summary_jobs_by_key,
            progress=progress,
            fallback_details=fallback_details,
        )

        pending_calls = [
            TargetCall(
                target_key=summary_job['target_key'],
                invoke=summary_job['generate'],
            )
            for summary_job in summary_jobs
            if summary_job['target_key'] not in progress.completed_targets
        ]
        await run_target_calls(pending_calls, on_result=persist_result)
        context.ai_attempted_count = context.ai_target_count

        if fallback_details:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='AI summaries generated with fallback responses.',
                context_json={
                    'fallbackCount': len(fallback_details),
                    'fallbackDetails': fallback_details,
                },
            )

        context.log_messages.append(
            f'Generated {context.generated_summary_count} AI summary row(s).'
        )
        return context


def _add_summary_job(
    summary_jobs: list[_SummaryJob],
    *,
    summary_type: str,
    market_type: str | None,
    cluster_id: int | None,
    generate: Callable[[], Awaitable[dict[str, Any]]],
) -> None:
    summary_jobs.append(
        {
            'summary_type': summary_type,
            'market_type': market_type,
            'cluster_id': cluster_id,
            'target_key': build_ai_summary_target_key(
                summary_type,
                market_type=market_type,
                cluster_id=cluster_id,
            ),
            'generate': generate,
        }
    )


async def _add_cluster_summary_jobs(
    summary_jobs: list[_SummaryJob],
    *,
    cluster_repo: Any,
    llm_provider: BatchLlmProvider,
    bounded_generate: Callable[..., Awaitable[dict]],
    market_type: str,
    cluster: dict,
) -> None:
    cluster_articles = await cluster_repo.get_cluster_articles(cluster['id'])
    processed_articles = await cluster_repo.get_processed_articles(
        [row['processed_article_id'] for row in cluster_articles]
    )
    _add_summary_job(
        summary_jobs,
        summary_type=AiSummaryType.CLUSTER_CARD_SUMMARY.value,
        market_type=market_type,
        cluster_id=cluster['id'],
        generate=partial(
            bounded_generate,
            _generate_cluster_card_summary,
            llm_provider,
            market_type,
            cluster,
            processed_articles,
        ),
    )
    _add_summary_job(
        summary_jobs,
        summary_type=AiSummaryType.CLUSTER_DETAIL_ANALYSIS.value,
        market_type=market_type,
        cluster_id=cluster['id'],
        generate=partial(
            bounded_generate,
            _generate_cluster_detail_summary,
            llm_provider,
            market_type,
            cluster,
            processed_articles,
        ),
    )


async def _build_summary_jobs(
    *,
    cluster_repo: Any,
    llm_provider: BatchLlmProvider,
    clusters: list[dict],
    indices: list,
    bounded_generate: Callable[..., Awaitable[dict]],
) -> list[_SummaryJob]:
    by_market: dict[str, list[dict]] = {}
    for cluster in clusters:
        by_market.setdefault(cluster['market_type'], []).append(cluster)
    indices_by_market: dict[str, list] = {}
    for index in indices:
        indices_by_market.setdefault(index.market_type, []).append(index)

    summary_jobs: list[_SummaryJob] = []
    _add_summary_job(
        summary_jobs,
        summary_type=AiSummaryType.GLOBAL_HEADLINE.value,
        market_type=None,
        cluster_id=None,
        generate=partial(
            bounded_generate,
            _generate_global_headline,
            llm_provider,
            clusters,
            indices,
        ),
    )
    for market_type, market_clusters in by_market.items():
        _add_summary_job(
            summary_jobs,
            summary_type=AiSummaryType.MARKET_SUMMARY.value,
            market_type=market_type,
            cluster_id=None,
            generate=partial(
                bounded_generate,
                _generate_market_summary,
                llm_provider,
                market_type=market_type,
                clusters=market_clusters,
                indices=indices_by_market.get(market_type, []),
            ),
        )
        for cluster in market_clusters:
            await _add_cluster_summary_jobs(
                summary_jobs,
                cluster_repo=cluster_repo,
                llm_provider=llm_provider,
                bounded_generate=bounded_generate,
                market_type=market_type,
                cluster=cluster,
            )

    return summary_jobs


def _build_fallback_report(
    payload: dict[str, Any], summary_job: _SummaryJob
) -> tuple[str, dict[str, Any]]:
    metadata = payload.get('metadata_json', {})
    error = metadata.get('error') if isinstance(metadata, dict) else None
    diagnostic = (
        error.get('message')
        if isinstance(error, dict)
        else payload.get('error_message') or 'LLM provider is not configured.'
    )
    summary_label = summary_job['summary_type']
    if summary_job['market_type']:
        summary_label = f'{summary_label}/{summary_job["market_type"]}'
    partial_reason = f'AI summary fallback for {summary_label}: {diagnostic}'
    fallback_detail = {
        'summaryType': summary_job['summary_type'],
        'marketType': summary_job['market_type'],
        'clusterId': summary_job['cluster_id'],
        'reason': metadata.get('reason') if isinstance(metadata, dict) else None,
        'error': error,
        'message': payload.get('error_message'),
    }
    return partial_reason, fallback_detail


async def _persist_summary_result(
    target_key: str,
    payload: dict[str, Any],
    *,
    context: BatchExecutionContext,
    repository: BatchJobRepository,
    step_code: str,
    summary_repo: Any,
    summary_jobs_by_key: dict[str, _SummaryJob],
    progress: DurableTargetProgress,
    fallback_details: list[dict[str, Any]],
) -> None:
    summary_job = summary_jobs_by_key[target_key]
    create_params = AiSummaryCreateParams(
        batch_job_id=context.job_id,
        summary_type=summary_job['summary_type'],
        business_date=context.business_date,
        market_type=summary_job['market_type'],
        cluster_id=summary_job['cluster_id'],
        title=payload.get('title'),
        body=payload.get('body'),
        paragraphs_json=payload.get('paragraphs', []),
        model_name=payload.get('model_name'),
        prompt_version=PROMPT_VERSION,
        status=payload['status'],
        fallback_used=payload['fallback_used'],
        error_message=payload.get('error_message'),
        metadata_json=payload.get('metadata_json', {}),
        target_key=target_key,
    )
    upsert = getattr(summary_repo, 'upsert_retry_summary', None)
    if callable(upsert):
        typed_upsert = cast(
            Callable[[AiSummaryCreateParams], Awaitable[Any]],
            upsert,
        )
        await typed_upsert(create_params)
    else:
        await summary_repo.insert_summary(create_params)

    context.generated_summary_count += 1
    if (
        payload['status'] == AiSummaryStatus.SUCCESS.value
        and not payload['fallback_used']
    ):
        context.ai_success_count += 1
    elif payload['status'] == AiSummaryStatus.FAILED.value:
        context.ai_failed_count += 1
    else:
        context.ai_fallback_count += 1
    context.fallback_count += int(payload['fallback_used'])
    if payload['fallback_used']:
        partial_reason, fallback_detail = _build_fallback_report(payload, summary_job)
        if partial_reason not in context.partial_reasons:
            context.partial_reasons.append(partial_reason)
        fallback_details.append(fallback_detail)
        await repository.add_event(
            job_id=context.job_id,
            step_code=step_code,
            level=EventLevel.INFO.value,
            message='AI summary target generated with fallback response.',
            context_json=fallback_detail,
        )
    await progress.commit_target(target_key, context)


__all__ = ['GenerateAiSummariesStep']
