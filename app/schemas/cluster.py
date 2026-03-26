from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


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
    articleCount: int | None = None


__all__ = [
    "ClusterArticleResponse",
    "ClusterDetailResponse",
    "ClusterSummaryResponse",
]
