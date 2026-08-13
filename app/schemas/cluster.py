from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from app.core.ai_contracts import (
    ANALYSIS_ISSUE_MESSAGES,
    ANALYSIS_SECTION_KIND_ORDER,
    ANALYSIS_SECTION_TITLES,
    validate_analysis_state_relationships,
)
from app.schemas.common import normalize_timestamp as _normalize_timestamp


class AnalysisSentenceResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    text: Annotated[str, Field(min_length=1)]
    sourceArticleIds: Annotated[list[StrictInt], Field(min_length=1)]
    conflictStatus: Literal['NOT_CHECKED', 'NONE', 'FOUND']
    conflictingSourceArticleIds: list[StrictInt]
    conflictNote: str | None

    @model_validator(mode='after')
    def validate_source_and_conflict_cardinality(self) -> Self:
        if not self.text.strip():
            raise ValueError('text must not be blank')
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
    model_config = ConfigDict(extra='forbid')

    sentences: Annotated[list[AnalysisSentenceResponse], Field(min_length=1)]


class _AnalysisSectionBase(BaseModel):
    model_config = ConfigDict(extra='forbid')


class BackgroundAnalysisSectionResponse(_AnalysisSectionBase):
    kind: Literal['background']
    title: Literal['발생 배경']
    paragraphs: Annotated[list[AnalysisParagraphResponse], Field(min_length=1)]


class ImpactAnalysisSectionResponse(_AnalysisSectionBase):
    kind: Literal['impact']
    title: Literal['시장 영향']
    paragraphs: Annotated[list[AnalysisParagraphResponse], Field(min_length=1)]


class RelatedAnalysisSectionResponse(_AnalysisSectionBase):
    kind: Literal['related']
    title: Literal['관련 업종·종목']
    paragraphs: Annotated[list[AnalysisParagraphResponse], Field(min_length=1)]


class OutlookAnalysisSectionResponse(_AnalysisSectionBase):
    kind: Literal['outlook']
    title: Literal['향후 관전 포인트']
    paragraphs: Annotated[list[AnalysisParagraphResponse], Field(min_length=1)]


AnalysisSectionResponse = Annotated[
    BackgroundAnalysisSectionResponse
    | ImpactAnalysisSectionResponse
    | RelatedAnalysisSectionResponse
    | OutlookAnalysisSectionResponse,
    Field(discriminator='kind'),
]


class AnalysisIssueResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: Literal[
        'ANALYSIS_GENERATION_FAILED',
        'NO_GROUNDED_SENTENCES',
        'INVALID_SOURCE_REFERENCE',
        'CONFLICT_CHECK_FAILED',
    ]
    message: str

    @model_validator(mode='after')
    def validate_approved_message(self) -> Self:
        if self.message != ANALYSIS_ISSUE_MESSAGES[self.code]:
            raise ValueError('analysis issue message does not match its code')
        return self


class ClusterSummaryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

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
        kinds = [section.kind for section in self.sections]
        if len(kinds) != len(set(kinds)):
            raise ValueError('analysis section kinds must be unique')
        if kinds != sorted(kinds, key=ANALYSIS_SECTION_KIND_ORDER.index):
            raise ValueError('analysis sections must use the fixed order')
        if any(
            section.title != ANALYSIS_SECTION_TITLES[section.kind]
            for section in self.sections
        ):
            raise ValueError('analysis sections must use fixed titles')

        issue_codes = [issue.code for issue in self.analysisIssues]
        if len(issue_codes) != len(set(issue_codes)):
            raise ValueError('analysis issue codes must be unique')

        if self.analysisStatus == 'UNAVAILABLE':
            if (
                self.sections
                or self.analysisGeneratedAt is not None
                or self.conflictStatus != 'NOT_CHECKED'
            ):
                raise ValueError(
                    'UNAVAILABLE requires empty sections, null generatedAt, '
                    'and NOT_CHECKED aggregate conflict status'
                )
            if 'CONFLICT_CHECK_FAILED' in issue_codes:
                raise ValueError(
                    'CONFLICT_CHECK_FAILED requires a retained NOT_CHECKED sentence'
                )
            return self

        if not self.sections:
            raise ValueError('displayable analysis requires nonempty sections')
        if self.analysisStatus == 'READY' and self.analysisIssues:
            raise ValueError('READY analysis cannot contain issues')
        if self.analysisStatus == 'PARTIAL' and not self.analysisIssues:
            raise ValueError('PARTIAL analysis requires at least one issue')

        sentences = [
            sentence
            for section in self.sections
            for paragraph in section.paragraphs
            for sentence in paragraph.sentences
        ]
        state_error = validate_analysis_state_relationships(
            status=self.analysisStatus,
            issue_codes=issue_codes,
            conflict_status=self.conflictStatus,
            sentence_statuses=(sentence.conflictStatus for sentence in sentences),
        )
        if state_error is not None:
            raise ValueError(state_error)
        return self


class ArticleGroupingIssueResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    code: Literal['SIMILARITY_GROUPING_FAILED']
    message: Literal['유사 기사 묶음을 생성하지 못했습니다.']


class ArticleGroupingResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

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
    model_config = ConfigDict(extra='forbid')

    processedArticleId: StrictInt
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
    model_config = ConfigDict(extra='forbid')

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
    'BackgroundAnalysisSectionResponse',
    'ClusterArticleResponse',
    'ClusterDetailResponse',
    'ClusterSummaryResponse',
    'ImpactAnalysisSectionResponse',
    'OutlookAnalysisSectionResponse',
    'RelatedAnalysisSectionResponse',
]
