from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.schemas.page import (
    ArticleLinkResponse,
    ClusterCardResponse,
    DailyPageResponse,
    IndexCardResponse,
    MarketAnalysisResponse,
    MarketMetadataResponse,
    MarketSectionResponse,
    PageMetadataResponse,
    RepresentativeArticleResponse,
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


def assemble_daily_page_response(payload: dict[str, Any]) -> DailyPageResponse:
    return DailyPageResponse.model_validate(payload)


def build_daily_page_response(
    page: dict[str, Any],
    markets: list[dict[str, Any]],
    indices: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    article_links: list[dict[str, Any]],
) -> DailyPageResponse:
    indices_by_market: dict[int, list[IndexCardResponse]] = defaultdict(list)
    for row in indices:
        indices_by_market[row["page_market_id"]].append(
            IndexCardResponse(
                indexCode=row["index_code"],
                indexName=row["index_name"],
                closePrice=row["close_price"],
                changeValue=row["change_value"],
                changePercent=row["change_percent"],
                highPrice=row.get("high_price"),
                lowPrice=row.get("low_price"),
            )
        )

    clusters_by_market: dict[int, list[ClusterCardResponse]] = defaultdict(list)
    for row in clusters:
        clusters_by_market[row["page_market_id"]].append(
            ClusterCardResponse(
                clusterId=str(row["cluster_uid"]),
                title=row["title"],
                summary=row.get("summary"),
                articleCount=row["article_count"],
                tags=list(row.get("tags_json") or []),
                representativeArticle=RepresentativeArticleResponse(
                    title=row.get("representative_title"),
                    publisherName=row.get("representative_publisher_name"),
                    publishedAt=_as_iso(row.get("representative_published_at")),
                    originLink=row.get("representative_origin_link"),
                    naverLink=row.get("representative_naver_link"),
                ),
            )
        )

    article_links_by_market: dict[int, list[ArticleLinkResponse]] = defaultdict(list)
    for row in article_links:
        article_links_by_market[row["page_market_id"]].append(
            ArticleLinkResponse(
                processedArticleId=row.get("processed_article_id"),
                clusterId=str(row["cluster_uid"]) if row.get("cluster_uid") else None,
                clusterTitle=row.get("cluster_title"),
                title=row["title"],
                publisherName=row.get("publisher_name"),
                publishedAt=_as_iso(row.get("published_at")),
                originLink=row["origin_link"],
                naverLink=row.get("naver_link"),
            )
        )

    market_sections = []
    for market in sorted(markets, key=lambda item: item["display_order"]):
        market_id = market["id"]
        market_sections.append(
            MarketSectionResponse(
                marketType=market["market_type"],
                marketLabel=market["market_label"],
                summaryTitle=market.get("summary_title"),
                summaryBody=market.get("summary_body"),
                analysis=MarketAnalysisResponse(
                    background=list(market.get("analysis_background_json") or []),
                    keyThemes=list(market.get("analysis_key_themes_json") or []),
                    outlook=market.get("analysis_outlook"),
                ),
                indices=indices_by_market[market_id],
                topClusters=clusters_by_market[market_id],
                articleLinks=article_links_by_market[market_id],
                metadata=MarketMetadataResponse(
                    rawNewsCount=market["raw_news_count"],
                    processedNewsCount=market["processed_news_count"],
                    clusterCount=market["cluster_count"],
                    lastUpdatedAt=_as_iso(market["last_updated_at"]),
                    partialMessage=market.get("partial_message"),
                ),
            )
        )

    return DailyPageResponse(
        pageId=page["id"],
        businessDate=_as_date(page["business_date"]),
        versionNo=page["version_no"],
        pageTitle=page["page_title"],
        status=page["status"],
        globalHeadline=page.get("global_headline"),
        generatedAt=_as_iso(page["generated_at"]),
        partialMessage=page.get("partial_message"),
        markets=market_sections,
        metadata=PageMetadataResponse(
            rawNewsCount=page["raw_news_count"],
            processedNewsCount=page["processed_news_count"],
            clusterCount=page["cluster_count"],
            lastUpdatedAt=_as_iso(page["last_updated_at"]),
        ),
    )


__all__ = ["assemble_daily_page_response", "build_daily_page_response"]
