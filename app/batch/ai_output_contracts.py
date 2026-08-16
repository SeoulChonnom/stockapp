"""Pure normalization for untrusted AI summary and analysis payloads."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from types import MappingProxyType
from typing import Any, Final

from app.core.ai_contracts import (
    ANALYSIS_ISSUE_MESSAGES,
    ANALYSIS_SECTION_KIND_ORDER,
    ANALYSIS_SECTION_TITLES,
    aggregate_conflict_status,
)
from app.core.plain_text import is_complete_plain_sentence

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
# A fixed title is worth enforcing; the exact codepoint a model picks for its
# punctuation is not. Every one of these reads as the same separator, and
# rejecting a whole analysis because the model wrote U+318D where the contract
# says U+00B7 discards work over a character no reader can tell apart.
_SECTION_TITLE_PUNCTUATION_FOLD: Final = str.maketrans(
    dict.fromkeys('ㆍ‧•⋅∙・･/··', '·')
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


def build_unavailable_analysis(
    *issue_codes: str, reason: str | None = None
) -> AnalysisResult:
    """Build the single public fallback shape with stable, unique issue codes.

    ``reason`` names the rule that rejected the payload. It is deliberately not
    one of the public keys -- callers copy only the three of those -- but
    without it the twelve distinct malformations below all reach the persisted
    row and the log as one ANALYSIS_GENERATION_FAILED, which says nothing about
    what to change. The strings come from this module's own vocabulary, never
    from provider text, so they are safe to record.
    """
    # CONFLICT_CHECK_FAILED describes a retained sentence degraded to
    # NOT_CHECKED.  An UNAVAILABLE response has no retained sentences, so that
    # issue would create an impossible public model state.
    if any(
        code not in ANALYSIS_ISSUE_MESSAGES or code == 'CONFLICT_CHECK_FAILED'
        for code in issue_codes
    ):
        issue_codes = ('ANALYSIS_GENERATION_FAILED',)
    result: AnalysisResult = {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': _issues_for(issue_codes),
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
    }
    if reason is not None:
        result['failureReason'] = reason
    return result


def canonical_key_point_issue(value: object) -> dict[str, str] | None:
    """Return the fixed public key-point issue for its approved code only."""
    if not isinstance(value, Mapping):
        return None
    if value.get('code') != KEY_POINT_FAILURE['code']:
        return None
    return dict(KEY_POINT_FAILURE)


def validate_analysis_sections(
    payload: object,
    valid_article_ids: Set[int],
) -> AnalysisResult:
    """Normalize analysis sections while isolating invalid evidence to a sentence."""
    if not isinstance(payload, Mapping):
        return build_unavailable_analysis(
            'ANALYSIS_GENERATION_FAILED', reason='payload_not_object'
        )
    sections_payload = payload.get('sections')
    if not isinstance(sections_payload, list):
        return build_unavailable_analysis(
            'ANALYSIS_GENERATION_FAILED', reason='sections_not_list'
        )

    structural_sections, structure_reason = _validate_section_structure(
        sections_payload
    )
    if structural_sections is None:
        return build_unavailable_analysis(
            'ANALYSIS_GENERATION_FAILED', reason=structure_reason
        )

    issue_codes: list[str] = []
    normalized_sections: list[dict[str, object]] = []

    for section in structural_sections:
        normalized_paragraphs: list[dict[str, object]] = []
        for paragraph in section['paragraphs']:
            normalized_sentences: list[dict[str, object]] = []
            for sentence in paragraph['sentences']:
                # A sentence with no usable text is dropped like any other
                # ungroundable one, which is what ``_normalize_analysis_sentence``
                # already does with it. Failing the whole analysis here threw
                # away every sound sentence in the cluster over a single empty
                # one, against this function's own stated contract.
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
        return build_unavailable_analysis(*issue_codes, reason='no_grounded_sentences')

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
    if not is_complete_plain_sentence(text):
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
) -> tuple[list[dict[str, object]] | None, str | None]:
    """Validate the section shape, naming the rule that rejects it."""
    normalized_sections: list[dict[str, object]] = []
    kinds: list[str] = []
    for section in sections:
        if not isinstance(section, Mapping):
            return None, 'section_not_object'
        kind = section.get('kind')
        paragraphs = section.get('paragraphs')
        if not isinstance(kind, str) or kind not in ANALYSIS_SECTION_TITLES:
            return None, 'section_kind_unknown'
        if not _matches_section_title(section.get('title'), kind):
            return None, 'section_title_mismatch'
        if not isinstance(paragraphs, list):
            return None, 'section_paragraphs_not_list'
        normalized_paragraphs: list[dict[str, object]] = []
        for paragraph in paragraphs:
            if not isinstance(paragraph, Mapping):
                return None, 'paragraph_not_object'
            sentences = paragraph.get('sentences')
            if not isinstance(sentences, list):
                return None, 'paragraph_sentences_not_list'
            normalized_sentences: list[dict[str, object]] = []
            for sentence in sentences:
                if not isinstance(sentence, Mapping):
                    return None, 'sentence_not_object'
                normalized_sentences.append(dict(sentence))
            normalized_paragraphs.append({'sentences': normalized_sentences})
        kinds.append(kind)
        normalized_sections.append(
            # The canonical title is stored rather than the model's spelling of
            # it, so an accepted variant cannot reach the persisted row, where
            # the public schema still requires the exact string.
            {
                'kind': kind,
                'title': ANALYSIS_SECTION_TITLES[kind],
                'paragraphs': normalized_paragraphs,
            }
        )
    if len(kinds) != len(set(kinds)):
        return None, 'section_kind_duplicated'
    if kinds != sorted(kinds, key=ANALYSIS_SECTION_KIND_ORDER.index):
        return None, 'section_order_invalid'
    return normalized_sections, None


def _matches_section_title(title: object, kind: str) -> bool:
    """Accept the fixed title through the spacing and punctuation models vary."""
    if not isinstance(title, str):
        return False
    return _folded_title(title) == _folded_title(ANALYSIS_SECTION_TITLES[kind])


def _folded_title(value: str) -> str:
    return ''.join(value.translate(_SECTION_TITLE_PUNCTUATION_FOLD).split())


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
    normalized_sentence = {
        'text': text,
        'sourceArticleIds': list(source_article_ids),
        'conflictStatus': conflict_status,
        'conflictingSourceArticleIds': list(conflicting_ids),
        'conflictNote': conflict_note,
    }
    issue_code = 'CONFLICT_CHECK_FAILED' if conflict_status == 'NOT_CHECKED' else None
    return normalized_sentence, issue_code


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
    'canonical_key_point_issue',
    'normalize_key_points',
    'validate_analysis_sections',
]
