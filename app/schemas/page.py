from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from app.core.timezone import isoformat_datetime


def _normalize_timestamp(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return isoformat_datetime(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
        return isoformat_datetime(parsed)
    return value


class RepresentativeArticleResponse(BaseModel):
    title: str | None = None
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str | None = None
    naverLink: str | None = None

    _normalize_published_at = field_validator('publishedAt', mode='before')(
        _normalize_timestamp
    )


class IndexCardResponse(BaseModel):
    indexCode: str
    indexName: str
    closePrice: Decimal
    changeValue: Decimal
    changePercent: Decimal
    highPrice: Decimal | None = None
    lowPrice: Decimal | None = None


class ClusterCardResponse(BaseModel):
    clusterId: str
    title: str
    summary: str | None = None
    articleCount: int
    tags: list[str]
    representativeArticle: RepresentativeArticleResponse


class ArticleLinkResponse(BaseModel):
    processedArticleId: int | None = None
    clusterId: str | None = None
    clusterTitle: str | None = None
    title: str
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str
    naverLink: str | None = None

    _normalize_published_at = field_validator('publishedAt', mode='before')(
        _normalize_timestamp
    )


class MarketAnalysisResponse(BaseModel):
    background: list[str]
    keyThemes: list[str]
    outlook: str | None = None


class MarketMetadataResponse(BaseModel):
    rawNewsCount: int
    processedNewsCount: int
    clusterCount: int
    lastUpdatedAt: datetime | str
    partialMessage: str | None = None

    _normalize_last_updated_at = field_validator('lastUpdatedAt', mode='before')(
        _normalize_timestamp
    )


class MarketSectionResponse(BaseModel):
    marketType: str
    marketLabel: str
    summaryTitle: str | None = None
    summaryBody: str | None = None
    analysis: MarketAnalysisResponse
    indices: list[IndexCardResponse]
    topClusters: list[ClusterCardResponse]
    articleLinks: list[ArticleLinkResponse]
    metadata: MarketMetadataResponse


class PageMetadataResponse(BaseModel):
    rawNewsCount: int
    processedNewsCount: int
    clusterCount: int
    lastUpdatedAt: datetime | str
    isLatest: bool = False

    _normalize_last_updated_at = field_validator('lastUpdatedAt', mode='before')(
        _normalize_timestamp
    )


class DailyPageResponse(BaseModel):
    pageId: int
    businessDate: date
    versionNo: int
    pageTitle: str
    status: str
    globalHeadline: str | None = None
    generatedAt: datetime | str
    partialMessage: str | None = None
    markets: list[MarketSectionResponse]
    metadata: PageMetadataResponse

    _normalize_generated_at = field_validator('generatedAt', mode='before')(
        _normalize_timestamp
    )


class ArchiveItemResponse(BaseModel):
    pageId: int
    businessDate: date
    pageTitle: str
    headlineSummary: str | None = None
    status: str
    generatedAt: datetime | str
    partialMessage: str | None = None

    _normalize_generated_at = field_validator('generatedAt', mode='before')(
        _normalize_timestamp
    )


class PaginationResponse(BaseModel):
    page: int
    size: int
    totalCount: int


class ArchiveListResponse(BaseModel):
    items: list[ArchiveItemResponse]
    pagination: PaginationResponse


__all__ = [
    'ArchiveItemResponse',
    'ArchiveListResponse',
    'ArticleLinkResponse',
    'ClusterCardResponse',
    'DailyPageResponse',
    'IndexCardResponse',
    'MarketAnalysisResponse',
    'MarketMetadataResponse',
    'MarketSectionResponse',
    'PageMetadataResponse',
    'PaginationResponse',
    'RepresentativeArticleResponse',
]
