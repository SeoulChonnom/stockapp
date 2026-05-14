from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.schemas.cluster import (
    ClusterArticleResponse,
    ClusterDetailResponse,
    ClusterSummaryResponse,
)


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def assemble_cluster_detail_response(payload: dict[str, Any]) -> ClusterDetailResponse:
    return ClusterDetailResponse.model_validate(payload)


def build_cluster_detail_response(
    cluster: dict[str, Any],
    representative_article: dict[str, Any],
    articles: list[dict[str, Any]],
) -> ClusterDetailResponse:
    return ClusterDetailResponse(
        clusterId=str(cluster['cluster_uid']),
        businessDate=_as_date(cluster['business_date']),
        marketType=cluster['market_type'],
        marketLabel='미국' if cluster['market_type'] == 'US' else '한국',
        title=cluster['title'],
        tags=list(cluster.get('tags_json') or []),
        summary=ClusterSummaryResponse(
            short=cluster.get('summary_short'),
            long=cluster.get('summary_long'),
            analysis=list(cluster.get('analysis_paragraphs_json') or []),
        ),
        representativeArticle=ClusterArticleResponse(
            processedArticleId=representative_article['id'],
            title=representative_article['canonical_title'],
            publisherName=representative_article.get('publisher_name'),
            publishedAt=_as_iso(representative_article.get('published_at')),
            originLink=representative_article['origin_link'],
            naverLink=representative_article.get('naver_link'),
        ),
        articles=[
            ClusterArticleResponse(
                processedArticleId=article['id'],
                title=article['canonical_title'],
                publisherName=article.get('publisher_name'),
                publishedAt=_as_iso(article.get('published_at')),
                originLink=article['origin_link'],
                naverLink=article.get('naver_link'),
            )
            for article in articles
        ],
        lastUpdatedAt=_as_iso(cluster['last_updated_at']),
        articleCount=cluster.get('article_count'),
    )


__all__ = ['assemble_cluster_detail_response', 'build_cluster_detail_response']
