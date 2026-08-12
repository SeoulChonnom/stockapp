from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from app.schemas.common import normalize_timestamp as _normalize_timestamp


class AnalysisSentenceResponse(BaseModel):
    text: str
    sourceArticleIds: list[int]
    conflictStatus: Literal['NOT_CHECKED', 'NONE', 'FOUND']
    conflictingSourceArticleIds: list[int]
    conflictNote: str | None

    @model_validator(mode='after')
    def validate_source_and_conflict_cardinality(self) -> Self:
        if not self.sourceArticleIds:
            raise ValueError('sourceArticleIds must not be empty')
        if len(self.sourceArticleIds) != len(set(self.sourceArticleIds)):
            raise ValueError('sourceArticleIds must be unique')
        if len(self.conflictingSourceArticleIds) != len(
            set(self.conflictingSourceArticleIds)
        ):
            raise ValueError('conflictingSourceArticleIds must be unique')
        if set(self.sourceArticleIds) & set(self.conflictingSourceArticleIds):
            raise ValueError('source and conflicting source IDs must be disjoint')

        if self.conflictStatus == 'FOUND':
            if not self.conflictingSourceArticleIds:
                raise ValueError('FOUND requires conflicting source IDs')
            if self.conflictNote is None or not self.conflictNote.strip():
                raise ValueError('FOUND requires a nonblank conflict note')
            return self

        if self.conflictingSourceArticleIds:
            raise ValueError('non-FOUND conflicts require an empty source ID list')
        if self.conflictNote is not None:
            raise ValueError('non-FOUND conflicts require a null note')
        return self


class AnalysisParagraphResponse(BaseModel):
    sentences: list[AnalysisSentenceResponse]


class AnalysisSectionResponse(BaseModel):
    kind: Literal['background', 'impact', 'related', 'outlook']
    title: str
    paragraphs: list[AnalysisParagraphResponse]


class AnalysisIssueResponse(BaseModel):
    code: Literal[
        'ANALYSIS_GENERATION_FAILED',
        'NO_GROUNDED_SENTENCES',
        'INVALID_SOURCE_REFERENCE',
        'CONFLICT_CHECK_FAILED',
    ]
    message: str


class ClusterSummaryResponse(BaseModel):
    short: str | None = None
    long: str | None = None
    analysisStatus: Literal['READY', 'PARTIAL', 'UNAVAILABLE']
    analysisGeneratedAt: datetime | str | None
    analysisIssues: list[AnalysisIssueResponse]
    conflictStatus: Literal['NOT_CHECKED', 'NONE', 'FOUND']
    sections: list[AnalysisSectionResponse]

    _normalize_analysis_generated_at = field_validator(
        'analysisGeneratedAt', mode='before'
    )(_normalize_timestamp)

    @model_validator(mode='after')
    def validate_analysis_state(self) -> Self:
        section_order = ['background', 'impact', 'related', 'outlook']
        section_titles = {
            'background': '발생 배경',
            'impact': '시장 영향',
            'related': '관련 업종·종목',
            'outlook': '향후 관전 포인트',
        }
        kinds = [section.kind for section in self.sections]
        if len(kinds) != len(set(kinds)):
            raise ValueError('analysis section kinds must be unique')
        if kinds != sorted(kinds, key=section_order.index):
            raise ValueError('analysis sections must use the fixed order')
        if any(
            section.title != section_titles[section.kind] for section in self.sections
        ):
            raise ValueError('analysis sections must use fixed titles')

        if self.analysisStatus == 'UNAVAILABLE' and (
            self.sections
            or self.analysisGeneratedAt is not None
            or self.conflictStatus != 'NOT_CHECKED'
        ):
            raise ValueError(
                'UNAVAILABLE requires empty sections, null generatedAt, '
                'and NOT_CHECKED aggregate conflict status'
            )
        return self


class ArticleGroupingIssueResponse(BaseModel):
    code: Literal['SIMILARITY_GROUPING_FAILED']
    message: Literal['유사 기사 묶음을 생성하지 못했습니다.']


class ArticleGroupingResponse(BaseModel):
    status: Literal['READY', 'UNAVAILABLE']
    generatedAt: datetime | str | None
    issue: ArticleGroupingIssueResponse | None

    _normalize_generated_at = field_validator('generatedAt', mode='before')(
        _normalize_timestamp
    )

    @model_validator(mode='after')
    def validate_unavailable_state(self) -> Self:
        if self.status == 'UNAVAILABLE' and (
            self.generatedAt is not None or self.issue is None
        ):
            raise ValueError(
                'UNAVAILABLE grouping requires null generatedAt and a public issue'
            )
        return self


class ClusterArticleResponse(BaseModel):
    processedArticleId: int
    title: str
    publisherName: str | None = None
    publishedAt: datetime | str | None = None
    originLink: str
    naverLink: str | None = None
    sourceSummary: str | None = None
    similarGroupId: str
    isSimilarGroupRepresentative: bool
    exactDuplicateCount: int = Field(ge=0)

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
    articleGrouping: ArticleGroupingResponse
    lastUpdatedAt: datetime | str
    articleCount: int

    _normalize_last_updated_at = field_validator('lastUpdatedAt', mode='before')(
        _normalize_timestamp
    )


__all__ = [
    'AnalysisIssueResponse',
    'AnalysisParagraphResponse',
    'AnalysisSectionResponse',
    'AnalysisSentenceResponse',
    'ArticleGroupingIssueResponse',
    'ArticleGroupingResponse',
    'ClusterArticleResponse',
    'ClusterDetailResponse',
    'ClusterSummaryResponse',
]
