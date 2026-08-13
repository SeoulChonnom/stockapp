from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.plain_text import is_complete_plain_sentence
from app.schemas.common import normalize_timestamp as _normalize_timestamp


def _require_complete_plain_sentence(value: str) -> str:
    if not is_complete_plain_sentence(value):
        raise ValueError('text must be one complete plain-text sentence')
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


class DirectionKeyPointResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['direction']
    label: Literal['시장 방향']
    text: str
    direction: Literal['UP', 'DOWN', 'MIXED', 'FLAT']

    _validate_text = field_validator('text')(_require_complete_plain_sentence)


class DriverKeyPointResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['driver']
    label: Literal['주요 원인']
    text: str

    _validate_text = field_validator('text')(_require_complete_plain_sentence)


class WatchKeyPointResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    kind: Literal['watch']
    label: Literal['관전 포인트']
    text: str

    _validate_text = field_validator('text')(_require_complete_plain_sentence)


KeyPointResponse = Annotated[
    DirectionKeyPointResponse | DriverKeyPointResponse | WatchKeyPointResponse,
    Field(discriminator='kind'),
]


class ArticleLinkResponse(BaseModel):
    processedArticleId: StrictInt
    clusterId: str | None = None
    clusterTitle: str | None = None
    title: str
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str
    naverLink: str | None = None
    similarGroupId: str
    isSimilarGroupRepresentative: bool
    exactDuplicateCount: int = Field(ge=0)

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
    keyPoints: list[KeyPointResponse]
    markets: list[MarketSectionResponse]
    metadata: PageMetadataResponse
    navigation: PageNavigationResponse
    versions: list[PageVersionSummaryResponse]

    _normalize_generated_at = field_validator('generatedAt', mode='before')(
        _normalize_timestamp
    )

    @model_validator(mode='after')
    def validate_key_points(self) -> Self:
        if not self.keyPoints:
            return self
        if [point.kind for point in self.keyPoints] != [
            'direction',
            'driver',
            'watch',
        ]:
            raise ValueError(
                'keyPoints must be empty or ordered direction, driver, watch'
            )
        return self


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
    'DirectionKeyPointResponse',
    'DriverKeyPointResponse',
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
    'WatchKeyPointResponse',
]
