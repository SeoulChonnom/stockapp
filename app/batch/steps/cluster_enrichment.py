from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.batch.logging import log_safe_exception
from app.batch.normalizers import normalize_title, tokenize_text
from app.batch.providers.llm_provider import BatchLlmProvider
from app.core.llm import LlmRetryableError
from app.core.public_diagnostics import (
    public_ai_invalid_response,
    public_ai_provider_error,
)

LOGGER = logging.getLogger(__name__)


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


def _group_articles(
    articles: list, *, max_articles_per_group: int | None = None
) -> list[list]:
    """Group articles by title-token overlap against each group's seed article.

    Tokens are compared against the tokens of the article that opened the group,
    never against a set accumulated from every member. Accumulating widened a
    group's token set on every merge, so a large group matched almost any title
    through shared common vocabulary and absorbed the market's whole feed into
    one bucket. ``max_articles_per_group`` is a backstop for a genuinely large
    news day: a group at the cap stops accepting members instead of growing
    without bound.
    """
    groups: list[list] = []
    group_tokens: list[set[str]] = []
    for article in sorted(
        articles,
        key=lambda candidate: candidate.processed_article_id,
    ):
        article_tokens = set(tokenize_text(article.canonical_title))
        matched_index: int | None = None
        for group_index, tokens in enumerate(group_tokens):
            if (
                max_articles_per_group is not None
                and len(groups[group_index]) >= max_articles_per_group
            ):
                continue
            if article_tokens and len(article_tokens.intersection(tokens)) >= 2:
                matched_index = group_index
                break
        if matched_index is None:
            groups.append([article])
            group_tokens.append(set(article_tokens))
        else:
            groups[matched_index].append(article)
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


def _build_enrichment_payload(articles: list) -> list[dict[str, Any]]:
    return [
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


def _build_cluster_fallback(articles: list) -> dict[str, Any]:
    representative = articles[0]
    return {
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


def _parse_enrichment_response(
    result: object, articles: list
) -> tuple[str | None, list | None, list | None, int]:
    if not isinstance(result, dict):
        return 'Cluster enrichment response must be an object.', None, None, 0
    tags = result.get('tags')
    if tags is not None and not isinstance(tags, list):
        return 'Cluster enrichment tags must be a list.', None, None, 0
    analysis_paragraphs = result.get('analysis_paragraphs')
    if analysis_paragraphs is not None and not isinstance(analysis_paragraphs, list):
        return 'Cluster enrichment analysis_paragraphs must be a list.', None, None, 0
    try:
        representative_index = int(result.get('representative_article_index', 0) or 0)
    except TypeError, ValueError:
        return (
            'Cluster enrichment representative_article_index must be an integer.',
            None,
            None,
            0,
        )
    if representative_index < 0 or representative_index >= len(articles):
        representative_index = 0
    return None, tags, analysis_paragraphs, representative_index


async def _enrich_cluster(
    llm_provider: BatchLlmProvider,
    market_type: str,
    articles: list,
) -> dict:
    fallback = _build_cluster_fallback(articles)
    if not llm_provider.is_configured():
        return fallback
    try:
        result = await llm_provider.enrich_cluster(
            market_type=market_type,
            articles=_build_enrichment_payload(articles),
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
        fallback['error_context'] = public_ai_provider_error(exc)
        return fallback

    reason, tags, analysis_paragraphs, representative_index = (
        _parse_enrichment_response(result, articles)
    )
    if reason:
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = public_ai_invalid_response()
        return fallback

    result_mapping = result if isinstance(result, dict) else {}
    representative_article_id = articles[representative_index].processed_article_id
    return {
        'title': result_mapping.get('title') or fallback['title'],
        'summary_short': result_mapping.get('summary_short')
        or fallback['summary_short'],
        'summary_long': result_mapping.get('summary_long') or fallback['summary_long'],
        'tags': fallback['tags'] if tags is None else tags,
        'analysis_paragraphs': (
            fallback['analysis_paragraphs']
            if analysis_paragraphs is None
            else analysis_paragraphs
        ),
        'representative_article_id': representative_article_id,
        'fallback_used': False,
        'fallback_reason': 'llm',
        'error_context': None,
    }


__all__ = [
    '_derive_tags',
    '_enrich_cluster',
    '_group_articles',
    '_rank_market_clusters',
]
