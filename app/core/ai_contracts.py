"""Immutable constants shared by AI normalization and response schemas."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from types import MappingProxyType
from typing import Final

ANALYSIS_SECTION_KIND_ORDER: Final = ('background', 'impact', 'related', 'outlook')
ANALYSIS_SECTION_TITLES: Final = MappingProxyType(
    {
        'background': '발생 배경',
        'impact': '시장 영향',
        'related': '관련 업종·종목',
        'outlook': '향후 관전 포인트',
    }
)
ANALYSIS_ISSUE_MESSAGES: Final = MappingProxyType(
    {
        'ANALYSIS_GENERATION_FAILED': '분석을 생성하지 못했습니다.',
        'NO_GROUNDED_SENTENCES': '근거를 확인할 수 있는 분석 문장이 없습니다.',
        'INVALID_SOURCE_REFERENCE': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
        'CONFLICT_CHECK_FAILED': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
    }
)
_DISPLAYABLE_ANALYSIS_STATUSES = frozenset({'READY', 'PARTIAL'})
_CONFLICT_STATUSES = frozenset({'NOT_CHECKED', 'NONE', 'FOUND'})
_CAUSAL_ANALYSIS_ISSUES = frozenset(
    {'INVALID_SOURCE_REFERENCE', 'CONFLICT_CHECK_FAILED'}
)


def aggregate_conflict_status(sentences: Iterable[Mapping[str, object]]) -> str:
    """Aggregate sentence conflict states using the public priority ordering."""
    has_not_checked = False
    has_none = False
    for sentence in sentences:
        status = sentence.get('conflictStatus')
        if status == 'FOUND':
            return 'FOUND'
        if status == 'NOT_CHECKED':
            has_not_checked = True
        elif status == 'NONE':
            has_none = True
    if has_not_checked:
        return 'NOT_CHECKED'
    if has_none:
        return 'NONE'
    return 'NOT_CHECKED'


def validate_analysis_state_relationships(
    *,
    status: str,
    issue_codes: Sequence[str],
    conflict_status: str,
    sentence_statuses: Iterable[str],
) -> str | None:
    """Return an error when displayable analysis state fields contradict."""
    if status not in _DISPLAYABLE_ANALYSIS_STATUSES:
        return 'analysis state must be displayable'
    if conflict_status not in _CONFLICT_STATUSES:
        return 'analysis conflict status is invalid'
    if len(issue_codes) != len(set(issue_codes)):
        return 'analysis issue codes must be unique'

    statuses = list(sentence_statuses)
    has_not_checked = 'NOT_CHECKED' in statuses
    issue_set = set(issue_codes)
    if status == 'READY' and issue_codes:
        return 'READY analysis cannot contain issues'
    if status == 'READY' and has_not_checked:
        return 'READY analysis cannot contain NOT_CHECKED sentences'
    if status == 'PARTIAL' and (
        not issue_codes or not issue_set <= _CAUSAL_ANALYSIS_ISSUES
    ):
        return 'PARTIAL analysis requires degradation issues only'
    if ('CONFLICT_CHECK_FAILED' in issue_set) != has_not_checked:
        return 'CONFLICT_CHECK_FAILED must match NOT_CHECKED sentences'

    aggregate = aggregate_conflict_status(
        {'conflictStatus': sentence_status} for sentence_status in statuses
    )
    if conflict_status != aggregate:
        return 'aggregate conflict status does not match sentences'
    return None


__all__ = [
    'ANALYSIS_ISSUE_MESSAGES',
    'ANALYSIS_SECTION_KIND_ORDER',
    'ANALYSIS_SECTION_TITLES',
    'aggregate_conflict_status',
    'validate_analysis_state_relationships',
]
