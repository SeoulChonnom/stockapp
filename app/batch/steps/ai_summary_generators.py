from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.batch.ai_output_contracts import (
    build_unavailable_analysis,
    normalize_key_points,
    validate_analysis_sections,
)
from app.batch.logging import log_safe_exception
from app.batch.providers.llm_provider import BatchLlmProvider
from app.core.llm import LlmRetryableError
from app.core.public_diagnostics import (
    AI_PROVIDER_FAILURE_MESSAGE,
    public_ai_invalid_response,
    public_ai_provider_error,
)
from app.db.enums import AiSummaryStatus

LOGGER = logging.getLogger(__name__)


def _with_malformed_fallback(fallback: dict[str, Any], reason: str) -> dict[str, Any]:
    _ = reason
    return {
        **fallback,
        'error_message': public_ai_invalid_response()['message'],
        'metadata_json': {
            **fallback.get('metadata_json', {}),
            'reason': 'llm_malformed_response',
            'error': public_ai_invalid_response(),
        },
    }


def _is_optional_string(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _normalize_string_list_fields(
    result: object,
    *,
    field_names: tuple[str, ...],
) -> object:
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    for field_name in field_names:
        value = normalized.get(field_name)
        if isinstance(value, str):
            stripped = value.strip()
            normalized[field_name] = [stripped] if stripped else []
    return normalized


def _normalize_string_fields(
    result: object,
    *,
    field_names: tuple[str, ...],
) -> object:
    if not isinstance(result, dict):
        return result
    normalized = dict(result)
    for field_name in field_names:
        value = normalized.get(field_name)
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            normalized[field_name] = '\n\n'.join(
                item.strip() for item in value if item.strip()
            )
    return normalized


def _as_summary_mapping(result: object) -> dict[str, Any]:
    """Narrow an already-validated summary result to a mapping."""
    return result if isinstance(result, dict) else {}


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


async def _run_llm_summary(
    llm_provider: BatchLlmProvider,
    *,
    fallback: dict[str, Any],
    request: Callable[[], Awaitable[Any]],
    validate: Callable[[object], str | None],
    build_success: Callable[[dict[str, Any], str | None], dict[str, Any]],
    normalize: Callable[[object], object] | None = None,
    log_message: str,
) -> dict[str, Any]:
    """Run the common fallback/validate/success skeleton for an LLM summary call."""
    if not llm_provider.is_configured():
        return fallback
    model_name = getattr(llm_provider, 'model_name', None)
    try:
        result = await request()
        if normalize is not None:
            result = normalize(result)
        malformed_reason = validate(result)
        if malformed_reason:
            return _with_malformed_fallback(fallback, malformed_reason)
        return build_success(_as_summary_mapping(result), model_name)
    except LlmRetryableError:
        raise
    except Exception as exc:
        log_safe_exception(LOGGER, logging.WARNING, log_message, exception=exc)
        fallback['error_message'] = AI_PROVIDER_FAILURE_MESSAGE
        fallback['metadata_json'] = {
            **fallback['metadata_json'],
            'error': public_ai_provider_error(exc),
        }
        return fallback


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

    def build_success(
        payload: dict[str, Any], model_name: str | None
    ) -> dict[str, Any]:
        return {
            'title': payload.get('title') or fallback_title,
            'body': payload.get('body'),
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {'reason': 'llm'},
        }

    return await _run_llm_summary(
        llm_provider,
        fallback=fallback,
        request=lambda: llm_provider.summarize_global_headline(
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
        ),
        validate=lambda result: _validate_summary_result(
            result, summary_name='Global headline', string_fields=('title', 'body')
        ),
        build_success=build_success,
        log_message='Global headline provider request failed.',
    )


async def _generate_key_points(
    llm_provider: BatchLlmProvider, clusters: list[dict], indices: list
) -> dict[str, object]:
    if not llm_provider.is_configured():
        normalized = normalize_key_points(None)
        return {
            'keyPoints': normalized['keyPoints'],
            'issue': normalized.get('issue'),
        }

    try:
        result = await llm_provider.summarize_key_points(
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
    except LlmRetryableError:
        raise
    except Exception as exc:
        log_safe_exception(
            LOGGER,
            logging.WARNING,
            'Key point provider request failed.',
            exception=exc,
        )
        result = None

    payload = result.get('keyPoints') if isinstance(result, dict) else None
    normalized = normalize_key_points(payload)
    return {
        'keyPoints': normalized['keyPoints'],
        'issue': normalized.get('issue'),
    }


async def _generate_global_outputs(
    llm_provider: BatchLlmProvider, clusters: list[dict], indices: list
) -> dict[str, Any]:
    headline_result = await _generate_global_headline(
        llm_provider,
        clusters,
        indices,
    )
    key_point_result = await _generate_key_points(
        llm_provider,
        clusters,
        indices,
    )
    headline_metadata = headline_result.get('metadata_json') or {}
    return {
        **headline_result,
        'metadata_json': {
            **headline_metadata,
            'keyPoints': key_point_result['keyPoints'],
            'keyPointIssue': key_point_result['issue'],
        },
    }


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

    def normalize(result: object) -> object:
        return _normalize_string_list_fields(
            result, field_names=('background', 'key_themes')
        )

    def build_success(
        payload: dict[str, Any], model_name: str | None
    ) -> dict[str, Any]:
        return {
            'title': payload.get('title') or fallback['title'],
            'body': payload.get('body') or fallback['body'],
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {
                'background': payload.get('background')
                or fallback['metadata_json']['background'],
                'keyThemes': payload.get('key_themes')
                or fallback['metadata_json']['keyThemes'],
                'outlook': payload.get('outlook')
                or fallback['metadata_json']['outlook'],
            },
        }

    return await _run_llm_summary(
        llm_provider,
        fallback=fallback,
        request=lambda: llm_provider.summarize_market(
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
        ),
        validate=lambda result: _validate_summary_result(
            result,
            summary_name='Market summary',
            string_fields=('title', 'body', 'outlook'),
            list_fields=('background', 'key_themes'),
        ),
        build_success=build_success,
        normalize=normalize,
        log_message='Market summary provider request failed.',
    )


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

    def build_success(
        payload: dict[str, Any], model_name: str | None
    ) -> dict[str, Any]:
        return {
            'title': payload.get('title') or fallback['title'],
            'body': payload.get('body') or fallback['body'],
            'status': AiSummaryStatus.SUCCESS.value,
            'fallback_used': False,
            'model_name': model_name,
            'metadata_json': {'reason': 'llm'},
        }

    return await _run_llm_summary(
        llm_provider,
        fallback=fallback,
        request=lambda: llm_provider.summarize_cluster_card(
            market_type=market_type,
            cluster={'title': cluster['title'], 'summary': cluster['summary_short']},
            articles=articles,
        ),
        validate=lambda result: _validate_summary_result(
            result, summary_name='Cluster card summary', string_fields=('title', 'body')
        ),
        build_success=build_success,
        log_message='Cluster card summary provider request failed.',
    )


async def _generate_cluster_detail_summary(
    llm_provider: BatchLlmProvider,
    market_type: str,
    cluster: dict,
    articles: list[dict],
) -> dict:
    unavailable = build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
    fallback = {
        'title': cluster['title'],
        'body': cluster['summary_long'] or cluster['summary_short'],
        'paragraphs': [],
        'status': AiSummaryStatus.FALLBACK.value,
        'fallback_used': True,
        'metadata_json': {
            'analysisStatus': unavailable['analysisStatus'],
            'analysisIssues': unavailable['analysisIssues'],
            'conflictStatus': unavailable['conflictStatus'],
        },
    }
    if not llm_provider.is_configured():
        return fallback

    prompt_articles = [
        {
            'processedArticleId': article['id'],
            'title': article.get('canonical_title'),
            'summary': article.get('source_summary'),
            'excerpt': article.get('article_body_excerpt'),
        }
        for article in articles
        if isinstance(article.get('id'), int)
        and not isinstance(article.get('id'), bool)
    ]
    valid_article_ids = {article['processedArticleId'] for article in prompt_articles}
    try:
        result = await llm_provider.summarize_cluster_detail(
            market_type=market_type,
            cluster={
                'title': cluster['title'],
                'summary': cluster['summary_long'] or cluster['summary_short'],
            },
            articles=prompt_articles,
        )
    except LlmRetryableError:
        raise
    except Exception as exc:
        log_safe_exception(
            LOGGER,
            logging.WARNING,
            'Cluster detail summary provider request failed.',
            exception=exc,
        )
        return {
            **fallback,
            'error_message': AI_PROVIDER_FAILURE_MESSAGE,
        }

    analysis = validate_analysis_sections(result, valid_article_ids)
    metadata = {
        'analysisStatus': analysis['analysisStatus'],
        'analysisIssues': analysis['analysisIssues'],
        'conflictStatus': analysis['conflictStatus'],
    }
    if analysis['analysisStatus'] == 'UNAVAILABLE':
        error_message = None
        if any(
            issue.get('code') == 'ANALYSIS_GENERATION_FAILED'
            for issue in analysis['analysisIssues']
        ):
            error_message = public_ai_invalid_response()['message']
        return {
            **fallback,
            'error_message': error_message,
            'metadata_json': metadata,
        }
    return {
        'title': fallback['title'],
        'body': fallback['body'],
        'paragraphs': analysis['sections'],
        'status': AiSummaryStatus.SUCCESS.value,
        'fallback_used': False,
        'model_name': getattr(llm_provider, 'model_name', None),
        'metadata_json': metadata,
    }


__all__ = [
    '_generate_cluster_card_summary',
    '_generate_cluster_detail_summary',
    '_generate_global_headline',
    '_generate_global_outputs',
    '_generate_key_points',
    '_generate_market_summary',
]
