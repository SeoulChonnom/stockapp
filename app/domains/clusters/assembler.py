from __future__ import annotations

from datetime import date, datetime
from typing import Any

from app.core.timezone import isoformat_datetime
from app.schemas.cluster import (
    AnalysisIssueResponse,
    ArticleGroupingIssueResponse,
    ArticleGroupingResponse,
    ClusterArticleResponse,
    ClusterDetailResponse,
    ClusterSummaryResponse,
)


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
        return isoformat_datetime(parsed)
    if isinstance(value, datetime):
        return isoformat_datetime(value)
    return str(value)


def _as_required_iso(value: Any) -> str:
    iso = _as_iso(value)
    if iso is None:
        raise ValueError('required datetime value is missing')
    return iso


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _build_unavailable_group_article(
    article: dict[str, Any],
    *,
    cluster_uid: str,
    group_rank: int,
) -> ClusterArticleResponse:
    return ClusterArticleResponse(
        processedArticleId=article['id'],
        title=article['canonical_title'],
        publisherName=article.get('publisher_name'),
        publishedAt=_as_iso(article.get('published_at')),
        originLink=article['origin_link'],
        naverLink=article.get('naver_link'),
        sourceSummary=article.get('source_summary'),
        similarGroupId=f'sim-{cluster_uid}-{group_rank}',
        isSimilarGroupRepresentative=True,
        exactDuplicateCount=0,
    )


def assemble_cluster_detail_response(payload: dict[str, Any]) -> ClusterDetailResponse:
    return ClusterDetailResponse.model_validate(payload)


def build_cluster_detail_payload(
    cluster: dict[str, Any],
    representative_article: dict[str, Any],
    articles: list[dict[str, Any]],
) -> dict[str, Any]:
    cluster_uid = str(cluster['cluster_uid'])
    group_ranks_by_article_id = {
        article['id']: group_rank
        for group_rank, article in enumerate(articles, start=1)
    }
    representative_group_rank = group_ranks_by_article_id[representative_article['id']]
    return ClusterDetailResponse(
        clusterId=cluster_uid,
        businessDate=_as_date(cluster['business_date']),
        marketType=cluster['market_type'],
        marketLabel='미국' if cluster['market_type'] == 'US' else '한국',
        title=cluster['title'],
        tags=list(cluster.get('tags_json') or []),
        summary=ClusterSummaryResponse(
            short=cluster.get('summary_short'),
            long=cluster.get('summary_long'),
            analysisStatus='UNAVAILABLE',
            analysisGeneratedAt=None,
            analysisIssues=[
                AnalysisIssueResponse(
                    code='NO_GROUNDED_SENTENCES',
                    message='근거를 확인할 수 있는 분석 문장이 없습니다.',
                )
            ],
            conflictStatus='NOT_CHECKED',
            sections=[],
        ),
        representativeArticle=_build_unavailable_group_article(
            representative_article,
            cluster_uid=cluster_uid,
            group_rank=representative_group_rank,
        ),
        articles=[
            _build_unavailable_group_article(
                article,
                cluster_uid=cluster_uid,
                group_rank=group_rank,
            )
            for group_rank, article in enumerate(articles, start=1)
        ],
        articleGrouping=ArticleGroupingResponse(
            status='UNAVAILABLE',
            generatedAt=None,
            issue=ArticleGroupingIssueResponse(
                code='SIMILARITY_GROUPING_FAILED',
                message='유사 기사 묶음을 생성하지 못했습니다.',
            ),
        ),
        lastUpdatedAt=_as_required_iso(cluster['last_updated_at']),
        articleCount=cluster['article_count'],
    ).model_dump(mode='json')


__all__ = ['assemble_cluster_detail_response', 'build_cluster_detail_payload']
