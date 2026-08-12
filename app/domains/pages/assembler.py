from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from typing import Any

from app.core.public_diagnostics import sanitize_public_diagnostic
from app.core.timezone import isoformat_datetime
from app.schemas.page import (
    ArticleLinkResponse,
    ClusterCardResponse,
    DailyPageResponse,
    IndexCardResponse,
    MarketAnalysisResponse,
    MarketMetadataResponse,
    MarketSectionResponse,
    PageIssueResponse,
    PageMetadataResponse,
    PageNavigationResponse,
    PageVersionSummaryResponse,
    RepresentativeArticleResponse,
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


def _as_optional_date(value: Any) -> date | None:
    return None if value is None else _as_date(value)


def _build_navigation(neighbors: dict[str, Any]) -> PageNavigationResponse:
    return PageNavigationResponse(
        previousBusinessDate=_as_optional_date(neighbors.get('previous_business_date')),
        nextBusinessDate=_as_optional_date(neighbors.get('next_business_date')),
    )


def _sanitize_page_issues(raw_issues: Any) -> list[dict[str, str]]:
    """Defensively parse and sanitize a page's structured issue list.

    ``raw_issues`` comes from a free-form JSONB column (or from a
    previously-serialized response payload during the second sanitization
    pass), so it is treated as untrusted: entries that are not dicts, that
    are missing ``category``/``code``/``message``, or whose values are not
    strings, are silently dropped rather than raised. Every surviving
    ``message`` is routed through ``sanitize_public_diagnostic`` -- an
    entry whose message sanitizes away entirely is dropped too.
    """
    if not isinstance(raw_issues, list):
        return []
    sanitized: list[dict[str, str]] = []
    for entry in raw_issues:
        if not isinstance(entry, dict):
            continue
        category = entry.get('category')
        code = entry.get('code')
        message = entry.get('message')
        if not (
            isinstance(category, str)
            and isinstance(code, str)
            and isinstance(message, str)
        ):
            continue
        safe_message = sanitize_public_diagnostic(message)
        if not safe_message:
            continue
        sanitized.append({'category': category, 'code': code, 'message': safe_message})
    return sanitized


def _page_issues_from_metadata(metadata_json: Any) -> list[dict[str, str]]:
    if not isinstance(metadata_json, dict):
        return []
    return _sanitize_page_issues(metadata_json.get('issues'))


def _build_versions(
    versions: list[dict[str, Any]],
) -> list[PageVersionSummaryResponse]:
    return [
        PageVersionSummaryResponse(
            pageId=row['id'],
            versionNo=row['version_no'],
            status=row['status'],
            generatedAt=_as_required_iso(row['generated_at']),
            isLatest=bool(row['is_latest']),
        )
        for row in versions
    ]


def assemble_daily_page_response(payload: dict[str, Any]) -> DailyPageResponse:
    safe_markets = []
    for market in payload.get('markets', []):
        metadata = dict(market.get('metadata') or {})
        metadata['partialMessage'] = sanitize_public_diagnostic(
            metadata.get('partialMessage')
        )
        safe_markets.append({**market, 'metadata': metadata})
    return DailyPageResponse.model_validate(
        {
            **payload,
            'partialMessage': sanitize_public_diagnostic(payload.get('partialMessage')),
            'markets': safe_markets,
            'issues': _sanitize_page_issues(payload.get('issues')),
        }
    )


def build_daily_page_payload(
    page: dict[str, Any],
    markets: list[dict[str, Any]],
    indices: list[dict[str, Any]],
    clusters: list[dict[str, Any]],
    article_links: list[dict[str, Any]],
    *,
    neighbors: dict[str, Any],
    versions: list[dict[str, Any]],
) -> dict[str, Any]:
    indices_by_market: dict[int, list[IndexCardResponse]] = defaultdict(list)
    for row in indices:
        indices_by_market[row['page_market_id']].append(
            IndexCardResponse(
                indexCode=row['index_code'],
                indexName=row['index_name'],
                closePrice=row['close_price'],
                changeValue=row['change_value'],
                changePercent=row['change_percent'],
                highPrice=row.get('high_price'),
                lowPrice=row.get('low_price'),
                sourceDate=row.get('source_date'),
                expectedSessionDate=row.get('expected_session_date'),
                sessionCloseAt=_as_iso(row.get('session_close_at')),
            )
        )

    clusters_by_market: dict[int, list[ClusterCardResponse]] = defaultdict(list)
    for row in clusters:
        clusters_by_market[row['page_market_id']].append(
            ClusterCardResponse(
                clusterId=str(row['cluster_uid']),
                title=row['title'],
                summary=row.get('summary'),
                articleCount=row['article_count'],
                tags=list(row.get('tags_json') or []),
                representativeArticle=RepresentativeArticleResponse(
                    title=row.get('representative_title'),
                    publisherName=row.get('representative_publisher_name'),
                    publishedAt=_as_iso(row.get('representative_published_at')),
                    originLink=row.get('representative_origin_link'),
                    naverLink=row.get('representative_naver_link'),
                ),
            )
        )

    article_links_by_market: dict[int, list[ArticleLinkResponse]] = defaultdict(list)
    for row in article_links:
        article_links_by_market[row['page_market_id']].append(
            ArticleLinkResponse(
                processedArticleId=row.get('processed_article_id'),
                clusterId=str(row['cluster_uid']) if row.get('cluster_uid') else None,
                clusterTitle=row.get('cluster_title'),
                title=row['title'],
                publisherName=row.get('publisher_name'),
                publishedAt=_as_iso(row.get('published_at')),
                originLink=row['origin_link'],
                naverLink=row.get('naver_link'),
            )
        )

    market_sections = []
    for market in sorted(markets, key=lambda item: item['display_order']):
        market_id = market['id']
        market_sections.append(
            MarketSectionResponse(
                marketType=market['market_type'],
                marketLabel=market['market_label'],
                summaryTitle=market.get('summary_title'),
                summaryBody=market.get('summary_body'),
                analysis=MarketAnalysisResponse(
                    background=list(market.get('analysis_background_json') or []),
                    keyThemes=list(market.get('analysis_key_themes_json') or []),
                    outlook=market.get('analysis_outlook'),
                ),
                indices=indices_by_market[market_id],
                topClusters=clusters_by_market[market_id],
                articleLinks=article_links_by_market[market_id],
                metadata=MarketMetadataResponse(
                    rawNewsCount=market['raw_news_count'],
                    processedNewsCount=market['processed_news_count'],
                    clusterCount=market['cluster_count'],
                    lastUpdatedAt=_as_required_iso(market['last_updated_at']),
                    partialMessage=sanitize_public_diagnostic(
                        market.get('partial_message')
                    ),
                    sourceDate=market.get('actual_index_source_date'),
                    expectedSessionDate=market.get('expected_session_date'),
                    sessionCloseAt=_as_iso(market.get('session_close_at')),
                    newsWindowStartAt=_as_iso(market.get('news_window_start_at')),
                    newsWindowEndAt=_as_iso(market.get('news_window_end_at')),
                    coverageComplete=market.get('news_coverage_complete'),
                ),
            )
        )

    return DailyPageResponse(
        pageId=page['id'],
        businessDate=_as_date(page['business_date']),
        versionNo=page['version_no'],
        pageTitle=page['page_title'],
        status=page['status'],
        globalHeadline=page.get('global_headline'),
        generatedAt=_as_required_iso(page['generated_at']),
        partialMessage=sanitize_public_diagnostic(page.get('partial_message')),
        issues=[
            PageIssueResponse(**entry)
            for entry in _page_issues_from_metadata(page.get('metadata_json'))
        ],
        markets=market_sections,
        metadata=PageMetadataResponse(
            rawNewsCount=page['raw_news_count'],
            processedNewsCount=page['processed_news_count'],
            clusterCount=page['cluster_count'],
            lastUpdatedAt=_as_required_iso(page['last_updated_at']),
            isLatest=bool(page.get('is_latest', False)),
        ),
        navigation=_build_navigation(neighbors),
        versions=_build_versions(versions),
    ).model_dump(mode='json')


__all__ = ['assemble_daily_page_response', 'build_daily_page_payload']
