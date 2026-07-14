from __future__ import annotations

from datetime import date, datetime
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


class ClusterSummaryResponse(BaseModel):
    short: str | None = None
    long: str | None = None
    analysis: list[str]


class ClusterArticleResponse(BaseModel):
    processedArticleId: int | None = None
    title: str
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str
    naverLink: str | None = None
    sourceSummary: str | None = None

    _normalize_published_at = field_validator('publishedAt', mode='before')(
        _normalize_timestamp
    )


class ClusterDetailResponse(BaseModel):
    clusterId: str
    businessDate: date
    marketType: str
    marketLabel: str
    title: str
    tags: list[str]
    summary: ClusterSummaryResponse
    representativeArticle: ClusterArticleResponse
    articles: list[ClusterArticleResponse]
    lastUpdatedAt: datetime | str
    articleCount: int

    _normalize_last_updated_at = field_validator('lastUpdatedAt', mode='before')(
        _normalize_timestamp
    )


__all__ = [
    'ClusterArticleResponse',
    'ClusterDetailResponse',
    'ClusterSummaryResponse',
]
