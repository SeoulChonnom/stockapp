"""Pure normalization for untrusted AI summary and analysis payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from types import MappingProxyType
from typing import Any, Final

type KeyPoint = dict[str, str]
type AnalysisResult = dict[str, Any]

KEY_POINT_KIND_ORDER: Final = ('direction', 'driver', 'watch')
KEY_POINT_LABELS: Final = MappingProxyType(
    {
        'direction': '시장 방향',
        'driver': '주요 원인',
        'watch': '관전 포인트',
    }
)
KEY_POINT_FAILURE: Final = MappingProxyType(
    {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }
)
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


def normalize_key_points(payload: object) -> dict[str, object]:
    """Return contract-compliant key points or the one public fallback issue."""
    if not isinstance(payload, list) or len(payload) != len(KEY_POINT_KIND_ORDER):
        return _key_point_failure()

    key_points: list[KeyPoint] = []
    for item, expected_kind in zip(payload, KEY_POINT_KIND_ORDER, strict=True):
        normalized = _normalize_key_point(item, expected_kind)
        if normalized is None:
            return _key_point_failure()
        key_points.append(normalized)
    return {'keyPoints': key_points}


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


def build_unavailable_analysis(*issue_codes: str) -> AnalysisResult:
    """Build the single public fallback shape with stable, unique issue codes."""
    return {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': _issues_for(issue_codes),
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
    }


def validate_analysis_sections(
    payload: object,
    valid_article_ids: Set[int],
) -> AnalysisResult:
    """Normalize analysis sections while isolating invalid evidence to a sentence."""
    if not isinstance(payload, Mapping):
        return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')
    sections_payload = payload.get('sections')
    if not isinstance(sections_payload, list):
        return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')

    structural_sections = _validate_section_structure(sections_payload)
    if structural_sections is None:
        return build_unavailable_analysis('ANALYSIS_GENERATION_FAILED')

    issue_codes: list[str] = []
    normalized_sections: list[dict[str, object]] = []

    for section in structural_sections:
        normalized_paragraphs: list[dict[str, object]] = []
        for paragraph in section['paragraphs']:
            normalized_sentences: list[dict[str, object]] = []
            for sentence in paragraph['sentences']:
                normalized_sentence, issue_code = _normalize_analysis_sentence(
                    sentence,
                    valid_article_ids,
                )
                if issue_code == 'INVALID_SOURCE_REFERENCE':
                    _append_issue(issue_codes, issue_code)
                    continue
                if issue_code is not None:
                    _append_issue(issue_codes, issue_code)
                if normalized_sentence is not None:
                    normalized_sentences.append(normalized_sentence)
            if normalized_sentences:
                normalized_paragraphs.append({'sentences': normalized_sentences})
        if normalized_paragraphs:
            normalized_sections.append(
                {
                    'kind': section['kind'],
                    'title': section['title'],
                    'paragraphs': normalized_paragraphs,
                }
            )

    flattened_sentences = [
        sentence
        for section in normalized_sections
        for paragraph in section['paragraphs']
        for sentence in paragraph['sentences']
    ]
    if not flattened_sentences:
        _append_issue(issue_codes, 'NO_GROUNDED_SENTENCES')
        return build_unavailable_analysis(*issue_codes)

    analysis_status = 'PARTIAL' if issue_codes else 'READY'
    return {
        'analysisStatus': analysis_status,
        'analysisIssues': _issues_for(issue_codes),
        'conflictStatus': aggregate_conflict_status(flattened_sentences),
        'sections': normalized_sections,
    }


def _normalize_key_point(item: object, expected_kind: str) -> KeyPoint | None:
    if not isinstance(item, Mapping):
        return None
    expected_keys = {'kind', 'label', 'text'}
    if expected_kind == 'direction':
        expected_keys.add('direction')
    if set(item) != expected_keys:
        return None
    if item.get('kind') != expected_kind:
        return None
    if item.get('label') != KEY_POINT_LABELS[expected_kind]:
        return None
    text = item.get('text')
    if not isinstance(text, str) or not text.strip():
        return None

    normalized: KeyPoint = {
        'kind': expected_kind,
        'label': KEY_POINT_LABELS[expected_kind],
        'text': text,
    }
    if expected_kind == 'direction':
        direction = item.get('direction')
        if not isinstance(direction, str) or direction not in {
            'UP',
            'DOWN',
            'MIXED',
            'FLAT',
        }:
            return None
        normalized['direction'] = direction
    return normalized


def _key_point_failure() -> dict[str, object]:
    return {'keyPoints': [], 'issue': dict(KEY_POINT_FAILURE)}


def _validate_section_structure(
    sections: list[object],
) -> list[dict[str, object]] | None:
    normalized_sections: list[dict[str, object]] = []
    kinds: list[str] = []
    for section in sections:
        if not isinstance(section, Mapping):
            return None
        kind = section.get('kind')
        title = section.get('title')
        paragraphs = section.get('paragraphs')
        if (
            not isinstance(kind, str)
            or kind not in ANALYSIS_SECTION_TITLES
            or title != ANALYSIS_SECTION_TITLES[kind]
            or not isinstance(paragraphs, list)
        ):
            return None
        normalized_paragraphs: list[dict[str, object]] = []
        for paragraph in paragraphs:
            if not isinstance(paragraph, Mapping):
                return None
            sentences = paragraph.get('sentences')
            if not isinstance(sentences, list):
                return None
            normalized_sentences: list[dict[str, object]] = []
            for sentence in sentences:
                if not isinstance(sentence, Mapping):
                    return None
                normalized_sentences.append(dict(sentence))
            normalized_paragraphs.append({'sentences': normalized_sentences})
        kinds.append(kind)
        normalized_sections.append(
            {'kind': kind, 'title': title, 'paragraphs': normalized_paragraphs}
        )
    if len(kinds) != len(set(kinds)):
        return None
    if kinds != sorted(kinds, key=ANALYSIS_SECTION_KIND_ORDER.index):
        return None
    return normalized_sections


def _normalize_analysis_sentence(
    sentence: Mapping[str, object],
    valid_article_ids: Set[int],
) -> tuple[dict[str, object] | None, str | None]:
    text = sentence.get('text')
    if not isinstance(text, str) or not text.strip():
        return None, 'INVALID_SOURCE_REFERENCE'

    source_article_ids = sentence.get('sourceArticleIds')
    if not _valid_article_id_list(
        source_article_ids, valid_article_ids, allow_empty=False
    ):
        return None, 'INVALID_SOURCE_REFERENCE'

    conflict_status = sentence.get('conflictStatus')
    conflicting_ids = sentence.get('conflictingSourceArticleIds')
    conflict_note = sentence.get('conflictNote')
    required_conflict_fields = {
        'conflictStatus',
        'conflictingSourceArticleIds',
        'conflictNote',
    }
    if not required_conflict_fields <= sentence.keys() or not _valid_conflict_fields(
        conflict_status,
        conflicting_ids,
        conflict_note,
        source_article_ids,
        valid_article_ids,
    ):
        return (
            {
                'text': text,
                'sourceArticleIds': list(source_article_ids),
                'conflictStatus': 'NOT_CHECKED',
                'conflictingSourceArticleIds': [],
                'conflictNote': None,
            },
            'CONFLICT_CHECK_FAILED',
        )
    return (
        {
            'text': text,
            'sourceArticleIds': list(source_article_ids),
            'conflictStatus': conflict_status,
            'conflictingSourceArticleIds': list(conflicting_ids),
            'conflictNote': conflict_note,
        },
        None,
    )


def _valid_article_id_list(
    article_ids: object,
    valid_article_ids: Set[int],
    *,
    allow_empty: bool,
) -> bool:
    if not isinstance(article_ids, list) or (not allow_empty and not article_ids):
        return False
    if any(
        not isinstance(article_id, int) or isinstance(article_id, bool)
        for article_id in article_ids
    ):
        return False
    return (
        len(article_ids) == len(set(article_ids))
        and set(article_ids) <= valid_article_ids
    )


def _valid_conflict_fields(
    status: object,
    conflicting_ids: object,
    note: object,
    source_article_ids: object,
    valid_article_ids: Set[int],
) -> bool:
    if not isinstance(status, str) or status not in {'NOT_CHECKED', 'NONE', 'FOUND'}:
        return False
    if not _valid_article_id_list(conflicting_ids, valid_article_ids, allow_empty=True):
        return False
    if set(source_article_ids) & set(conflicting_ids):
        return False
    if status == 'FOUND':
        return bool(conflicting_ids) and isinstance(note, str) and bool(note.strip())
    return not conflicting_ids and note is None


def _append_issue(issue_codes: list[str], code: str) -> None:
    if code not in issue_codes:
        issue_codes.append(code)


def _issues_for(issue_codes: Iterable[str]) -> list[dict[str, str]]:
    unique_codes: list[str] = []
    for code in issue_codes:
        if code in ANALYSIS_ISSUE_MESSAGES and code not in unique_codes:
            unique_codes.append(code)
    return [
        {'code': code, 'message': ANALYSIS_ISSUE_MESSAGES[code]}
        for code in unique_codes
    ]


__all__ = [
    'ANALYSIS_ISSUE_MESSAGES',
    'ANALYSIS_SECTION_KIND_ORDER',
    'ANALYSIS_SECTION_TITLES',
    'KEY_POINT_KIND_ORDER',
    'KEY_POINT_LABELS',
    'aggregate_conflict_status',
    'build_unavailable_analysis',
    'normalize_key_points',
    'validate_analysis_sections',
]
