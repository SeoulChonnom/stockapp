from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, field_validator

from app.schemas.common import normalize_timestamp as _normalize_timestamp


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
    sourceDate: date | None = None
    expectedSessionDate: date | None = None
    sessionCloseAt: datetime | str | None = None

    _normalize_session_close_at = field_validator('sessionCloseAt', mode='before')(
        _normalize_timestamp
    )


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
    sourceDate: date | None = None
    expectedSessionDate: date | None = None
    sessionCloseAt: datetime | str | None = None
    newsWindowStartAt: datetime | str | None = None
    newsWindowEndAt: datetime | str | None = None
    coverageComplete: bool | None = None

    _normalize_market_timestamps = field_validator(
        'lastUpdatedAt',
        'sessionCloseAt',
        'newsWindowStartAt',
        'newsWindowEndAt',
        mode='before',
    )(_normalize_timestamp)


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
    isLatest: bool

    _normalize_last_updated_at = field_validator('lastUpdatedAt', mode='before')(
        _normalize_timestamp
    )


class PageNavigationResponse(BaseModel):
    """Nearest business dates that actually have a page.

    Both fields are required but nullable: the client always receives the
    keys, and ``null`` means "no such neighbor exists", never "not computed".
    Calendar arithmetic on ``businessDate`` is not a valid substitute — the
    page table only holds dates a batch produced.
    """

    previousBusinessDate: date | None
    nextBusinessDate: date | None


class PageDateNavigationResponse(BaseModel):
    """Availability and nearest public page dates for a requested date."""

    businessDate: date
    pageExists: bool
    previousBusinessDate: date | None
    nextBusinessDate: date | None


class PageVersionSummaryResponse(BaseModel):
    pageId: int
    versionNo: int
    status: str
    generatedAt: datetime | str
    isLatest: bool

    _normalize_generated_at = field_validator('generatedAt', mode='before')(
        _normalize_timestamp
    )


class PageIssueResponse(BaseModel):
    """A single structured diagnostic behind ``partialMessage``.

    Sourced from the page's persisted ``metadata_json.issues``. Every
    ``message`` has already passed through ``sanitize_public_diagnostic``,
    so it is safe to render directly.
    """

    category: str
    code: str
    message: str


class DailyPageResponse(BaseModel):
    pageId: int
    businessDate: date
    versionNo: int
    pageTitle: str
    status: str
    globalHeadline: str | None = None
    generatedAt: datetime | str
    partialMessage: str | None = None
    issues: list[PageIssueResponse]
    markets: list[MarketSectionResponse]
    metadata: PageMetadataResponse
    navigation: PageNavigationResponse
    versions: list[PageVersionSummaryResponse]

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
    'PageIssueResponse',
    'PageDateNavigationResponse',
    'PageMetadataResponse',
    'PageNavigationResponse',
    'PageVersionSummaryResponse',
    'PaginationResponse',
    'RepresentativeArticleResponse',
]
