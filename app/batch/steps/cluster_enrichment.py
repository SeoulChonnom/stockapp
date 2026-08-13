from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.batch.logging import log_safe_exception
from app.batch.normalizers import normalize_title, tokenize_text
from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.theme_classifier import (
    ArticleEvidence,
    ThemeAssignment,
    ThemeEvidence,
    classify_theme_fallback,
)
from app.batch.theme_rules import ThemeRuleCatalog, load_theme_rules
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


def _parse_theme_codes(
    value: object,
    *,
    allowed_theme_codes: Sequence[str],
) -> list[str]:
    """Return valid leaf codes in provider order, capped at three items."""

    if not isinstance(value, list):
        return []
    allowed = set(allowed_theme_codes)
    parsed: list[str] = []
    for candidate in value:
        if not isinstance(candidate, str) or candidate not in allowed:
            continue
        if candidate in parsed:
            continue
        parsed.append(candidate)
        if len(parsed) == 3:
            break
    return parsed


def _build_theme_evidence(
    articles: list,
    *,
    cluster_title: str | None,
    representative_article_id: int,
) -> ThemeEvidence:
    """Build canonical source evidence for deterministic theme fallback."""

    return ThemeEvidence(
        cluster_title=cluster_title,
        representative_article_id=representative_article_id,
        articles=tuple(
            ArticleEvidence(
                article_id=article.processed_article_id,
                title=article.canonical_title,
                source_summary=article.source_summary,
                article_body_excerpt=article.article_body_excerpt,
            )
            for article in articles
        ),
    )


def _classify_theme_codes(
    result: object,
    *,
    articles: list,
    cluster_title: str | None,
    representative_article_id: int,
    theme_catalog: ThemeRuleCatalog,
) -> tuple[list[ThemeAssignment], bool]:
    """Use valid provider themes or independently classify with keyword rules."""

    result_mapping = result if isinstance(result, dict) else {}
    valid_codes = _parse_theme_codes(
        result_mapping.get('themeCodes'),
        allowed_theme_codes=theme_catalog.codes,
    )
    if valid_codes:
        return [
            ThemeAssignment(
                theme_code=code,
                rank=rank,
                classification_method='LLM',
            )
            for rank, code in enumerate(valid_codes, start=1)
        ], False

    evidence = _build_theme_evidence(
        articles,
        cluster_title=cluster_title,
        representative_article_id=representative_article_id,
    )
    return classify_theme_fallback(evidence, theme_catalog), True


def _with_theme_fallback(
    fallback: dict[str, Any],
    *,
    articles: list,
    theme_catalog: ThemeRuleCatalog,
) -> dict[str, Any]:
    assignments, _ = _classify_theme_codes(
        {},
        articles=articles,
        cluster_title=fallback['title'],
        representative_article_id=fallback['representative_article_id'],
        theme_catalog=theme_catalog,
    )
    fallback['theme_assignments'] = assignments
    fallback['theme_fallback_used'] = True
    return fallback


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
    *,
    theme_catalog: ThemeRuleCatalog | None = None,
) -> dict:
    selected_catalog = theme_catalog or load_theme_rules()
    fallback = _build_cluster_fallback(articles)
    if not llm_provider.is_configured():
        return _with_theme_fallback(
            fallback,
            articles=articles,
            theme_catalog=selected_catalog,
        )
    try:
        result = await llm_provider.enrich_cluster(
            market_type=market_type,
            articles=_build_enrichment_payload(articles),
            theme_codes=selected_catalog.codes,
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
        return _with_theme_fallback(
            fallback,
            articles=articles,
            theme_catalog=selected_catalog,
        )

    reason, tags, analysis_paragraphs, representative_index = (
        _parse_enrichment_response(result, articles)
    )
    if reason:
        fallback['fallback_reason'] = 'llm_malformed_response'
        fallback['error_context'] = public_ai_invalid_response()
        return _with_theme_fallback(
            fallback,
            articles=articles,
            theme_catalog=selected_catalog,
        )

    result_mapping = result if isinstance(result, dict) else {}
    representative_article_id = articles[representative_index].processed_article_id
    theme_assignments, theme_fallback_used = _classify_theme_codes(
        result,
        articles=articles,
        cluster_title=result_mapping.get('title') or fallback['title'],
        representative_article_id=representative_article_id,
        theme_catalog=selected_catalog,
    )
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
        'theme_assignments': theme_assignments,
        'theme_fallback_used': theme_fallback_used,
        'fallback_used': False,
        'fallback_reason': 'llm',
        'error_context': None,
    }


__all__ = [
    '_derive_tags',
    '_enrich_cluster',
    '_group_articles',
    '_parse_theme_codes',
    '_rank_market_clusters',
]
