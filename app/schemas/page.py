from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class RepresentativeArticleResponse(BaseModel):
    title: str | None = None
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str | None = None
    naverLink: str | None = None


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


class ArchiveItemResponse(BaseModel):
    pageId: int
    businessDate: date
    pageTitle: str
    headlineSummary: str | None = None
    status: str
    generatedAt: datetime | str
    partialMessage: str | None = None


class PaginationResponse(BaseModel):
    page: int
    size: int
    totalCount: int


class ArchiveListResponse(BaseModel):
    items: list[ArchiveItemResponse]
    pagination: PaginationResponse


__all__ = [
    "ArchiveItemResponse",
    "ArchiveListResponse",
    "ArticleLinkResponse",
    "ClusterCardResponse",
    "DailyPageResponse",
    "IndexCardResponse",
    "MarketAnalysisResponse",
    "MarketMetadataResponse",
    "MarketSectionResponse",
    "PageMetadataResponse",
    "PaginationResponse",
    "RepresentativeArticleResponse",
]
