"""Immutable constants shared by AI normalization and response schemas."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
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


__all__ = [
    'ANALYSIS_ISSUE_MESSAGES',
    'ANALYSIS_SECTION_KIND_ORDER',
    'ANALYSIS_SECTION_TITLES',
    'aggregate_conflict_status',
]
