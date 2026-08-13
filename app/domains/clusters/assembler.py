from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.batch.ai_output_contracts import (
    ANALYSIS_ISSUE_MESSAGES,
    build_unavailable_analysis,
    validate_analysis_sections,
)
from app.core.timezone import isoformat_datetime
from app.schemas.cluster import (
    ArticleGroupingIssueResponse,
    ArticleGroupingResponse,
    ClusterArticleResponse,
    ClusterDetailResponse,
    ClusterSummaryResponse,
)

_DISPLAYABLE_ANALYSIS_STATUSES = frozenset({'READY', 'PARTIAL'})
_CONFLICT_STATUSES = frozenset({'NOT_CHECKED', 'NONE', 'FOUND'})
_CAUSAL_ANALYSIS_ISSUES = frozenset(
    {'INVALID_SOURCE_REFERENCE', 'CONFLICT_CHECK_FAILED'}
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


def _build_unavailable_group_article(
    article: dict[str, Any],
    *,
    cluster_uid: str,
    group_rank: int,
) -> ClusterArticleResponse:
    return ClusterArticleResponse(
        processedArticleId=_required_article_id(article),
        title=article['canonical_title'],
        publisherName=article.get('publisher_name'),
        publishedAt=_as_iso(article.get('published_at')),
        originLink=article['origin_link'],
        naverLink=article.get('naver_link'),
        sourceSummary=article.get('source_summary'),
        similarGroupId=f'sim-{cluster_uid}-{group_rank}',
        isSimilarGroupRepresentative=True,
        exactDuplicateCount=0,
    )


def assemble_cluster_detail_response(payload: dict[str, Any]) -> ClusterDetailResponse:
    return ClusterDetailResponse.model_validate(payload)


def build_cluster_detail_payload(
    cluster: dict[str, Any],
    representative_article: dict[str, Any],
    articles: list[dict[str, Any]],
    ai_summary: Any | None = None,
) -> dict[str, Any]:
    cluster_uid = str(cluster['cluster_uid'])
    valid_article_ids = {_required_article_id(article) for article in articles}
    group_ranks_by_article_id = {
        _required_article_id(article): group_rank
        for group_rank, article in enumerate(articles, start=1)
    }
    representative_article_id = _required_article_id(representative_article)
    representative_group_rank = group_ranks_by_article_id[representative_article_id]
    analysis = _build_persisted_analysis(ai_summary, valid_article_ids)
    return ClusterDetailResponse(
        clusterId=cluster_uid,
        businessDate=_as_date(cluster['business_date']),
        marketType=cluster['market_type'],
        marketLabel='미국' if cluster['market_type'] == 'US' else '한국',
        title=cluster['title'],
        tags=list(cluster.get('tags_json') or []),
        summary=ClusterSummaryResponse(
            short=cluster.get('summary_short'),
            long=cluster.get('summary_long'),
            **analysis,
        ),
        representativeArticle=_build_unavailable_group_article(
            representative_article,
            cluster_uid=cluster_uid,
            group_rank=representative_group_rank,
        ),
        articles=[
            _build_unavailable_group_article(
                article,
                cluster_uid=cluster_uid,
                group_rank=group_rank,
            )
            for group_rank, article in enumerate(articles, start=1)
        ],
        articleGrouping=ArticleGroupingResponse(
            status='UNAVAILABLE',
            generatedAt=None,
            issue=ArticleGroupingIssueResponse(
                code='SIMILARITY_GROUPING_FAILED',
                message='유사 기사 묶음을 생성하지 못했습니다.',
            ),
        ),
        lastUpdatedAt=_as_required_iso(cluster['last_updated_at']),
        articleCount=cluster['article_count'],
    ).model_dump(mode='json')


def _required_article_id(article: Mapping[str, Any]) -> int:
    article_id = article.get('id')
    if not isinstance(article_id, int) or isinstance(article_id, bool):
        raise ValueError('processed article id is missing or invalid')
    return article_id


def _build_persisted_analysis(
    ai_summary: Any | None,
    valid_article_ids: set[int],
) -> dict[str, Any]:
    if ai_summary is None:
        return {
            **build_unavailable_analysis('ANALYSIS_GENERATION_FAILED'),
            'analysisGeneratedAt': None,
        }

    metadata = _as_mapping(_summary_value(ai_summary, 'metadata_json'))
    if not _is_successful_summary(ai_summary):
        return {
            **_unavailable_from_metadata(metadata),
            'analysisGeneratedAt': None,
        }

    persisted = validate_analysis_sections(
        {'sections': _summary_value(ai_summary, 'paragraphs_json')},
        valid_article_ids,
    )

    # A structural failure is authoritative.  Persisted metadata cannot turn a
    # malformed section tree into a displayable response (or leak its message).
    if _is_structural_analysis_failure(persisted):
        persisted = build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
    else:
        metadata_result = _validate_success_metadata(metadata, persisted)
        if metadata_result is None:
            persisted = build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
        else:
            metadata_status, metadata_issues = metadata_result
            persisted_codes = [issue['code'] for issue in persisted['analysisIssues']]
            issue_codes = _unique_issue_codes([*metadata_issues, *persisted_codes])
            if persisted['analysisStatus'] == 'UNAVAILABLE':
                persisted = build_unavailable_analysis(
                    *(issue_codes or ['ANALYSIS_GENERATION_FAILED']),
                )
            else:
                analysis_status = persisted['analysisStatus']
                if metadata_status == 'PARTIAL' or issue_codes:
                    analysis_status = 'PARTIAL'
                persisted = {
                    **persisted,
                    'analysisStatus': analysis_status,
                    'analysisIssues': _issues_for(issue_codes),
                }

    generated_at = (
        _summary_value(ai_summary, 'generated_at')
        if persisted['analysisStatus'] != 'UNAVAILABLE'
        else None
    )
    return {
        **persisted,
        'analysisGeneratedAt': generated_at,
    }


def _is_successful_summary(ai_summary: Any) -> bool:
    return _summary_value(ai_summary, 'status') == 'SUCCESS' and not bool(
        _summary_value(ai_summary, 'fallback_used', False)
    )


def _summary_value(summary: Any, key: str, default: Any = None) -> Any:
    if isinstance(summary, Mapping):
        return summary.get(key, default)
    return getattr(summary, key, default)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _is_structural_analysis_failure(persisted: Mapping[str, Any]) -> bool:
    return persisted.get('analysisStatus') == 'UNAVAILABLE' and any(
        issue.get('code') == 'ANALYSIS_GENERATION_FAILED'
        for issue in persisted.get('analysisIssues', [])
        if isinstance(issue, Mapping)
    )


def _validate_success_metadata(
    metadata: Mapping[str, Any],
    persisted: Mapping[str, Any],
) -> tuple[str, list[str]] | None:
    """Validate trusted metadata fields without trusting stored messages."""
    if not isinstance(metadata, dict):
        return None

    required_fields = {'analysisStatus', 'analysisIssues', 'conflictStatus'}
    if not required_fields <= metadata.keys():
        return None

    status = metadata['analysisStatus']
    conflict_status = metadata['conflictStatus']
    if (
        not isinstance(status, str)
        or not isinstance(conflict_status, str)
        or status not in _DISPLAYABLE_ANALYSIS_STATUSES
        or conflict_status not in _CONFLICT_STATUSES
    ):
        return None

    raw_issues = metadata['analysisIssues']
    if not isinstance(raw_issues, list):
        return None
    metadata_issues: list[str] = []
    for issue in raw_issues:
        if not isinstance(issue, Mapping):
            return None
        code = issue.get('code')
        if not isinstance(code, str) or code not in ANALYSIS_ISSUE_MESSAGES:
            return None
        if code not in metadata_issues:
            metadata_issues.append(code)

    if status == 'READY' and metadata_issues:
        return None
    if status == 'PARTIAL' and (
        not metadata_issues or not set(metadata_issues) <= _CAUSAL_ANALYSIS_ISSUES
    ):
        return None
    if conflict_status != persisted.get('conflictStatus'):
        return None
    return status, metadata_issues


def _unavailable_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    codes = _issue_codes(metadata.get('analysisIssues'))
    return build_unavailable_analysis(*(codes or ['ANALYSIS_GENERATION_FAILED']))


def _issue_codes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_issue_codes(
        [issue.get('code') for issue in value if isinstance(issue, Mapping)]
    )


def _unique_issue_codes(codes: list[Any]) -> list[str]:
    return [
        code
        for index, code in enumerate(codes)
        if isinstance(code, str)
        and code in ANALYSIS_ISSUE_MESSAGES
        and code not in codes[:index]
    ]


def _issues_for(codes: list[str]) -> list[dict[str, str]]:
    return [{'code': code, 'message': ANALYSIS_ISSUE_MESSAGES[code]} for code in codes]


__all__ = ['assemble_cluster_detail_response', 'build_cluster_detail_payload']
