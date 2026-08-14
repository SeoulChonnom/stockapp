from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.batch.ai_output_contracts import (
    ANALYSIS_ISSUE_MESSAGES,
    build_unavailable_analysis,
    validate_analysis_sections,
)
from app.core.ai_contracts import validate_analysis_state_relationships
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
_GROUPING_FAILED = 'SIMILARITY_GROUPING_FAILED'
_GROUPING_ISSUE_MESSAGE = '유사 기사 묶음을 생성하지 못했습니다.'


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
    is_representative: bool = True,
    exact_duplicate_count: int = 0,
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
        isSimilarGroupRepresentative=is_representative,
        exactDuplicateCount=exact_duplicate_count,
    )


def assemble_cluster_detail_response(payload: dict[str, Any]) -> ClusterDetailResponse:
    return ClusterDetailResponse.model_validate(payload)


def build_cluster_detail_payload(
    cluster: dict[str, Any],
    representative_article: dict[str, Any],
    articles: list[dict[str, Any]],
    ai_summary: Any | None = None,
    article_grouping: Any | None = None,
) -> dict[str, Any]:
    cluster_uid = str(cluster['cluster_uid'])
    article_ids = [_required_article_id(article) for article in articles]
    if len(article_ids) != len(set(article_ids)):
        raise ValueError('cluster detail contains duplicate processed article IDs')
    valid_article_ids = set(article_ids)
    representative_article_id = _required_article_id(representative_article)
    if representative_article_id not in valid_article_ids:
        raise ValueError('cluster representative article is not a cluster member')
    analysis = _build_persisted_analysis(ai_summary, valid_article_ids)
    grouping = _build_article_grouping(
        cluster,
        articles,
        article_grouping,
    )
    article_metadata = grouping['articles']

    def build_article(article: dict[str, Any]) -> ClusterArticleResponse:
        article_id = _required_article_id(article)
        group_rank, is_representative, exact_duplicate_count = article_metadata[
            article_id
        ]
        return _build_unavailable_group_article(
            article,
            cluster_uid=cluster_uid,
            group_rank=group_rank,
            is_representative=is_representative,
            exact_duplicate_count=exact_duplicate_count,
        )

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
        representativeArticle=build_article(representative_article),
        articles=[build_article(article) for article in articles],
        articleGrouping=grouping['response'],
        lastUpdatedAt=_as_required_iso(cluster['last_updated_at']),
        articleCount=cluster['article_count'],
    ).model_dump(mode='json')


def _build_article_grouping(
    cluster: Mapping[str, Any],
    articles: list[dict[str, Any]],
    grouping: Any | None,
) -> dict[str, Any]:
    article_ids = [_required_article_id(article) for article in articles]
    if grouping is None:
        metadata = {
            article_id: (rank, True, 0)
            for rank, article_id in enumerate(article_ids, start=1)
        }
        return {
            'response': ArticleGroupingResponse(
                status='UNAVAILABLE',
                generatedAt=None,
                issue=ArticleGroupingIssueResponse(
                    code=_GROUPING_FAILED,
                    message=_GROUPING_ISSUE_MESSAGE,
                ),
            ),
            'articles': metadata,
        }

    status = _grouping_value(grouping, 'status')
    if status not in {'READY', 'UNAVAILABLE'}:
        raise ValueError('cluster article grouping status is invalid')
    generated_at = _grouping_value(grouping, 'generated_at')
    if status == 'READY' and generated_at is None:
        raise ValueError('READY article grouping is missing generated_at')
    if status == 'UNAVAILABLE' and generated_at is not None:
        raise ValueError('UNAVAILABLE article grouping has generated_at')

    raw_groups = _grouping_value(grouping, 'groups')
    if not isinstance(raw_groups, (list, tuple)):
        raise ValueError('cluster article grouping groups are invalid')

    expected_ids = set(article_ids)
    metadata: dict[int, tuple[int, bool, int]] = {}
    seen_ids: set[int] = set()
    for expected_rank, group in enumerate(raw_groups, start=1):
        group_rank = _required_group_int(group, 'group_rank')
        if group_rank != expected_rank:
            raise ValueError('cluster article grouping ranks must be contiguous')
        group_id = _required_group_int(group, 'similar_group_id')
        raw_cluster_id = _grouping_value(group, 'cluster_id')
        if raw_cluster_id is not None and raw_cluster_id != cluster.get('id'):
            raise ValueError('cluster article grouping references another cluster')
        representative_id = _required_group_int(group, 'representative_article_id')
        raw_members = _grouping_value(group, 'members')
        if not isinstance(raw_members, (list, tuple)) or not raw_members:
            raise ValueError('cluster article grouping group has no members')
        representative_ids: list[int] = []
        for expected_article_rank, member in enumerate(raw_members, start=1):
            member_group_id = _required_group_int(member, 'similar_group_id')
            article_id = _required_group_int(member, 'processed_article_id')
            article_rank = _required_group_int(member, 'article_rank')
            if member_group_id != group_id or article_rank != expected_article_rank:
                raise ValueError('cluster article grouping member rank is invalid')
            if article_id not in expected_ids or article_id in seen_ids:
                raise ValueError('cluster article grouping membership is invalid')
            is_representative = _required_group_bool(member, 'is_representative')
            exact_duplicate_count = _required_nonnegative_group_int(
                member, 'exact_duplicate_count'
            )
            if is_representative:
                representative_ids.append(article_id)
            seen_ids.add(article_id)
            output_rank = expected_rank if status == 'READY' else 0
            metadata[article_id] = (
                output_rank,
                is_representative,
                exact_duplicate_count,
            )
        if representative_ids != [representative_id]:
            raise ValueError('cluster article grouping representative is invalid')
        if status == 'UNAVAILABLE' and len(raw_members) != 1:
            raise ValueError('UNAVAILABLE article groups must be singletons')

    if seen_ids != expected_ids or len(metadata) != len(article_ids):
        raise ValueError('cluster article grouping membership does not cover cluster')

    if status == 'UNAVAILABLE':
        ordered_metadata: dict[int, tuple[int, bool, int]] = {}
        for rank, article_id in enumerate(article_ids, start=1):
            _, is_representative, exact_duplicate_count = metadata[article_id]
            ordered_metadata[article_id] = (
                rank,
                is_representative,
                exact_duplicate_count,
            )
        metadata = ordered_metadata

    return {
        'response': ArticleGroupingResponse(
            status=status,
            generatedAt=generated_at,
            issue=(
                ArticleGroupingIssueResponse(
                    code=_GROUPING_FAILED,
                    message=_GROUPING_ISSUE_MESSAGE,
                )
                if status == 'UNAVAILABLE'
                else None
            ),
        ),
        'articles': metadata,
    }


def _grouping_value(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def _required_group_int(value: Any, key: str) -> int:
    candidate = _grouping_value(value, key)
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        raise ValueError(f'cluster article grouping {key} is invalid')
    return candidate


def _required_nonnegative_group_int(value: Any, key: str) -> int:
    candidate = _required_group_int(value, key)
    if candidate < 0:
        raise ValueError(f'cluster article grouping {key} is invalid')
    return candidate


def _required_group_bool(value: Any, key: str) -> bool:
    candidate = _grouping_value(value, key)
    if not isinstance(candidate, bool):
        raise ValueError(f'cluster article grouping {key} is invalid')
    return candidate


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
        if code in metadata_issues:
            return None
        metadata_issues.append(code)

    state_error = validate_analysis_state_relationships(
        status=status,
        issue_codes=metadata_issues,
        conflict_status=conflict_status,
        sentence_statuses=_persisted_sentence_statuses(persisted),
    )
    if state_error is not None:
        return None
    return status, metadata_issues


def _persisted_sentence_statuses(persisted: Mapping[str, Any]) -> list[str]:
    return [
        sentence['conflictStatus']
        for section in persisted.get('sections', [])
        for paragraph in section.get('paragraphs', [])
        for sentence in paragraph.get('sentences', [])
    ]


def _unavailable_from_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    raw_issues = metadata.get('analysisIssues')
    if not isinstance(raw_issues, list):
        return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')

    codes: list[str] = []
    for issue in raw_issues:
        if not isinstance(issue, Mapping):
            return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
        code = issue.get('code')
        if (
            not isinstance(code, str)
            or code not in ANALYSIS_ISSUE_MESSAGES
            or code in codes
            or code == 'CONFLICT_CHECK_FAILED'
        ):
            return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
        codes.append(code)
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
