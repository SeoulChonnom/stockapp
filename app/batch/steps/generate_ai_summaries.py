from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from app.batch.models import BatchExecutionContext
from app.batch.providers.llm_provider import PROMPT_VERSION, BatchLlmProvider
from app.batch.steps.base import BatchStep, require_repository_session
from app.db.enums import AiSummaryStatus, AiSummaryType, EventLevel
from app.db.repositories.ai_summary_write_repo import AiSummaryWriteRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.db.repositories.market_index_repo import MarketIndexRepository
from app.db.repositories.projections import AiSummaryCreateParams


def _llm_error_metadata(exc: Exception) -> dict[str, str]:
    return {
        'provider': 'BatchLlmProvider',
        'errorClass': type(exc).__name__,
        'errorMessage': str(exc),
    }


def _llm_malformed_metadata(reason: str) -> dict[str, str]:
    return {
        'provider': 'BatchLlmProvider',
        'errorClass': 'ValueError',
        'errorMessage': reason,
    }


def _with_malformed_fallback(fallback: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        **fallback,
        'error_message': reason,
        'metadata_json': {
            **fallback.get('metadata_json', {}),
            'reason': 'llm_malformed_response',
            'error': _llm_malformed_metadata(reason),
        },
    }


def _is_optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _validate_summary_result(
    result: object,
    *,
    summary_name: str,
    string_fields: tuple[str, ...] = (),
    list_fields: tuple[str, ...] = (),
) -> str | None:
    if not isinstance(result, dict):
        return f'{summary_name} response must be an object.'
    for field in string_fields:
        if not _is_optional_string(result.get(field)):
            return f'{summary_name} {field} must be a string.'
    for field in list_fields:
        value = result.get(field)
        if value is not None and not _is_string_list(value):
            return f'{summary_name} {field} must be a list of strings.'
    return None


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

        async def bounded_generate(callable_, *args, **kwargs) -> dict:
            async with semaphore:
                return await callable_(*args, **kwargs)

        by_market: dict[str, list[dict]] = {}
        for cluster in clusters:
            by_market.setdefault(cluster['market_type'], []).append(cluster)
        indices_by_market: dict[str, list] = {}
        for index in indices:
            indices_by_market.setdefault(index.market_type, []).append(index)

        summary_jobs: list[dict[str, Any]] = [
            {
                'summary_type': AiSummaryType.GLOBAL_HEADLINE.value,
                'market_type': None,
                'cluster_id': None,
                'payload': bounded_generate(
                    _generate_global_headline,
                    llm_provider,
                    clusters,
                    indices,
                ),
            }
        ]
        for market_type, market_clusters in by_market.items():
            summary_jobs.append(
                {
                    'summary_type': AiSummaryType.MARKET_SUMMARY.value,
                    'market_type': market_type,
                    'cluster_id': None,
                    'payload': bounded_generate(
                        _generate_market_summary,
                        llm_provider,
                        market_type=market_type,
                        clusters=market_clusters,
                        indices=indices_by_market.get(market_type, []),
                    ),
                }
            )
            for cluster in market_clusters:
                cluster_articles = await cluster_repo.get_cluster_articles(
                    cluster['id']
                )
                processed_articles = await cluster_repo.get_processed_articles(
                    [row['processed_article_id'] for row in cluster_articles]
                )
                summary_jobs.append(
                    {
                        'summary_type': AiSummaryType.CLUSTER_CARD_SUMMARY.value,
                        'market_type': market_type,
                        'cluster_id': cluster['id'],
                        'payload': bounded_generate(
                            _generate_cluster_card_summary,
                            llm_provider,
                            market_type,
                            cluster,
                            processed_articles,
                        ),
                    }
                )
                summary_jobs.append(
                    {
                        'summary_type': AiSummaryType.CLUSTER_DETAIL_ANALYSIS.value,
                        'market_type': market_type,
                        'cluster_id': cluster['id'],
                        'payload': bounded_generate(
                            _generate_cluster_detail_summary,
                            llm_provider,
                            market_type,
                            cluster,
                            processed_articles,
                        ),
                    }
                )

        payloads = await asyncio.gather(
            *(summary_job['payload'] for summary_job in summary_jobs)
        )
        step_fallback_count = 0
        fallback_details: list[dict[str, Any]] = []
        for summary_job, payload in zip(summary_jobs, payloads, strict=True):
            await summary_repo.insert_summary(
                AiSummaryCreateParams(
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
                )
            )
            context.generated_summary_count += 1
            context.fallback_count += int(payload['fallback_used'])
            if payload['fallback_used']:
                step_fallback_count += 1
                metadata = payload.get('metadata_json', {})
                error = metadata.get('error') if isinstance(metadata, dict) else None
                diagnostic = (
                    error.get('errorMessage')
                    if isinstance(error, dict)
                    else payload.get('error_message')
                    or 'LLM provider is not configured.'
                )
                summary_label = summary_job['summary_type']
                if summary_job['market_type']:
                    summary_label = (
                        f"{summary_label}/{summary_job['market_type']}"
                    )
                partial_reason = (
                    f'AI summary fallback for {summary_label}: {diagnostic}'
                )
                if partial_reason not in context.partial_reasons:
                    context.partial_reasons.append(partial_reason)
                fallback_details.append(
                    {
                        'summaryType': summary_job['summary_type'],
                        'marketType': summary_job['market_type'],
                        'clusterId': summary_job['cluster_id'],
                        'reason': metadata.get('reason')
                        if isinstance(metadata, dict)
                        else None,
                        'error': error,
                        'errorMessage': payload.get('error_message'),
                    }
                )

        if step_fallback_count:
            await repository.add_event(
                job_id=context.job_id,
                step_code=self.step_code,
                level=EventLevel.WARN.value,
                message='AI summaries generated with fallback responses.',
                context_json={
                    'fallbackCount': step_fallback_count,
                    'fallbackDetails': fallback_details,
                },
            )

        context.log_messages.append(
            f'Generated {context.generated_summary_count} AI summary row(s).'
        )
        return context


async def _generate_global_headline(
    llm_provider: BatchLlmProvider, clusters: list[dict], indices: list
) -> dict:
    fallback_title = '시장 주요 이슈를 종합한 글로벌 일간 요약'
    if clusters:
        fallback_title = ' / '.join(cluster['title'] for cluster in clusters[:2])
    fallback = {
        'title': fallback_title,
        'body': None,
        'status': AiSummaryStatus.FALLBACK.value,
        'fallback_used': True,
        'metadata_json': {'reason': 'llm_fallback'},
    }
    if not llm_provider.is_configured():
        return fallback
    model_name = getattr(llm_provider, 'model_name', None)
    try:
        result = await llm_provider.summarize_global_headline(
            clusters=[
                {'title': cluster['title'], 'summary': cluster['summary_short']}
                for cluster in clusters
            ],
            indices=[
                {
                    'marketType': index.market_type,
                    'indexCode': index.index_code,
                    'changePercent': str(index.change_percent),
                }
                for index in indices
            ],
        )
        malformed_reason = _validate_summary_result(
            result,
            summary_name='Global headline',
            string_fields=('title', 'body'),
        )
        if malformed_reason:
            return _with_malformed_fallback(fallback, malformed_reason)
        return {
            'title': result.get('title') or fallback_title,
            'body': result.get('body'),
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {'reason': 'llm'},
        }
    except Exception as exc:
        fallback['error_message'] = str(exc)
        fallback['metadata_json'] = {
            **fallback['metadata_json'],
            'error': _llm_error_metadata(exc),
        }
        return fallback


async def _generate_market_summary(
    llm_provider: BatchLlmProvider,
    *,
    market_type: str,
    clusters: list[dict],
    indices: list,
) -> dict:
    fallback = {
        'title': f'{market_type} 시장 핵심 이슈 요약',
        'body': (clusters[0]['summary_short'] if clusters else None),
        'status': AiSummaryStatus.FALLBACK.value,
        'fallback_used': True,
        'metadata_json': {
            'reason': 'llm_fallback',
            'background': [
                cluster['summary_short']
                for cluster in clusters[:2]
                if cluster['summary_short']
            ],
            'keyThemes': [
                tag
                for cluster in clusters[:2]
                for tag in (cluster.get('tags_json') or [])
            ][:5],
            'outlook': clusters[0]['summary_long'] if clusters else None,
        },
    }
    if not llm_provider.is_configured():
        return fallback
    model_name = getattr(llm_provider, 'model_name', None)
    try:
        result = await llm_provider.summarize_market(
            market_type=market_type,
            indices=[
                {
                    'indexCode': index.index_code,
                    'indexName': index.index_name,
                    'changePercent': str(index.change_percent),
                }
                for index in indices
            ],
            clusters=[
                {
                    'title': cluster['title'],
                    'summary': cluster['summary_short'],
                    'tags': cluster.get('tags_json') or [],
                }
                for cluster in clusters
            ],
        )
        malformed_reason = _validate_summary_result(
            result,
            summary_name='Market summary',
            string_fields=('title', 'body', 'outlook'),
            list_fields=('background', 'key_themes'),
        )
        if malformed_reason:
            return _with_malformed_fallback(fallback, malformed_reason)
        return {
            'title': result.get('title') or fallback['title'],
            'body': result.get('body') or fallback['body'],
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {
                'background': result.get('background')
                or fallback['metadata_json']['background'],
                'keyThemes': result.get('key_themes')
                or fallback['metadata_json']['keyThemes'],
                'outlook': result.get('outlook')
                or fallback['metadata_json']['outlook'],
            },
        }
    except Exception as exc:
        fallback['error_message'] = str(exc)
        fallback['metadata_json'] = {
            **fallback['metadata_json'],
            'error': _llm_error_metadata(exc),
        }
        return fallback


async def _generate_cluster_card_summary(
    llm_provider: BatchLlmProvider,
    market_type: str,
    cluster: dict,
    articles: list[dict],
) -> dict:
    fallback = {
        'title': cluster['title'],
        'body': cluster['summary_short'],
        'status': AiSummaryStatus.FALLBACK.value,
        'fallback_used': True,
        'metadata_json': {'reason': 'llm_fallback'},
    }
    if not llm_provider.is_configured():
        return fallback
    model_name = getattr(llm_provider, 'model_name', None)
    try:
        result = await llm_provider.summarize_cluster_card(
            market_type=market_type,
            cluster={'title': cluster['title'], 'summary': cluster['summary_short']},
            articles=articles,
        )
        malformed_reason = _validate_summary_result(
            result,
            summary_name='Cluster card summary',
            string_fields=('title', 'body'),
        )
        if malformed_reason:
            return _with_malformed_fallback(fallback, malformed_reason)
        return {
            'title': result.get('title') or fallback['title'],
            'body': result.get('body') or fallback['body'],
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {'reason': 'llm'},
        }
    except Exception as exc:
        fallback['error_message'] = str(exc)
        fallback['metadata_json'] = {
            **fallback['metadata_json'],
            'error': _llm_error_metadata(exc),
        }
        return fallback


async def _generate_cluster_detail_summary(
    llm_provider: BatchLlmProvider,
    market_type: str,
    cluster: dict,
    articles: list[dict],
) -> dict:
    fallback = {
        'title': cluster['title'],
        'body': cluster['summary_long'] or cluster['summary_short'],
        'paragraphs': cluster.get('analysis_paragraphs_json') or [],
        'status': AiSummaryStatus.FALLBACK.value,
        'fallback_used': True,
        'metadata_json': {'reason': 'llm_fallback'},
    }
    if not llm_provider.is_configured():
        return fallback
    model_name = getattr(llm_provider, 'model_name', None)
    try:
        result = await llm_provider.summarize_cluster_detail(
            market_type=market_type,
            cluster={
                'title': cluster['title'],
                'summary': cluster['summary_long'] or cluster['summary_short'],
            },
            articles=articles,
        )
        malformed_reason = _validate_summary_result(
            result,
            summary_name='Cluster detail summary',
            string_fields=('title', 'body'),
            list_fields=('paragraphs',),
        )
        if malformed_reason:
            return _with_malformed_fallback(fallback, malformed_reason)
        return {
            'title': result.get('title') or fallback['title'],
            'body': result.get('body') or fallback['body'],
            'paragraphs': result.get('paragraphs') or fallback['paragraphs'],
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {'reason': 'llm'},
        }
    except Exception as exc:
        fallback['error_message'] = str(exc)
        fallback['metadata_json'] = {
            **fallback['metadata_json'],
            'error': _llm_error_metadata(exc),
        }
        return fallback


__all__ = ['GenerateAiSummariesStep']
