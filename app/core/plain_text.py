"""Deterministic validation for public plain-text sentence fields."""

from __future__ import annotations

import re
from html import unescape

_HTML_TAG_RE = re.compile(r'<\s*/?\s*[A-Za-z][^>]*>')
_HTML_DECLARATION_RE = re.compile(r'<![^>]*>')
_HTML_PROCESSING_INSTRUCTION_RE = re.compile(r'<\?[^>]*\?>')
_MARKDOWN_HEADING_RE = re.compile(r'(?m)^\s{0,3}#{1,6}(?:\s|$)')
_MARKDOWN_LIST_RE = re.compile(r'(?m)^\s*(?:[-+*]|\d+[.)])\s+')
_MARKDOWN_LINK_RE = re.compile(r'!?\[[^\]\r\n]+\]\([^\)\r\n]*\)')
_MARKDOWN_REFERENCE_LINK_RE = re.compile(r'!?\[[^\]\r\n]+\]\[[^\]\r\n]*\]')
_MARKDOWN_REFERENCE_DEFINITION_RE = re.compile(r'(?m)^\s*\[[^\]\r\n]+\]:\s*\S+')
_MARKDOWN_EMPHASIS_RE = re.compile(
    r'(?:\*\*|__|\*|_|~~)(?=\S)[^\r\n]+?(?<=\S)(?:\*\*|__|\*|_|~~)'
)
_MARKDOWN_CODE_RE = re.compile(r'`{1,3}')
_MARKDOWN_QUOTE_RE = re.compile(r'(?m)^\s*>\s?')

_SENTENCE_TERMINATORS = frozenset('.!?。！？…')
_TERMINAL_CLOSING_PUNCTUATION = frozenset('」』》〉】〕）］〗〙〛〞〟')
_CLOSING_PUNCTUATION = frozenset('"\'”’»)]}」』》〉】〕）］〗〙〛〞〟')
_LINE_BREAKS = frozenset('\r\n\v\f\x85\u2028\u2029')
_COMMON_ABBREVIATIONS = frozenset(
    {
        'mr.',
        'mrs.',
        'ms.',
        'dr.',
        'prof.',
        'sr.',
        'jr.',
        'vs.',
        'etc.',
        'e.g.',
        'i.e.',
        'no.',
        'fig.',
        'u.s.',
        'u.s.a.',
    }
)


def is_complete_plain_sentence(value: object) -> bool:
    """Return whether ``value`` is one nonblank, plain-text sentence.

    The public B1 contract deliberately uses a conservative allow-list: text
    must end in sentence punctuation and may not contain markup, line breaks,
    or multiple sentence boundaries.  This function does not normalize text;
    callers keep the original string after validation.
    """
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or any(character in _LINE_BREAKS for character in value):
        return False
    if _contains_html_markup(text):
        return False
    if _contains_markdown_markup(text):
        return False
    if not any(character.isalnum() for character in text):
        return False

    sentence_text = text.rstrip(''.join(_CLOSING_PUNCTUATION))
    if not sentence_text or (
        sentence_text[-1] not in _SENTENCE_TERMINATORS
        and text[-1] not in _TERMINAL_CLOSING_PUNCTUATION
    ):
        return False
    return _sentence_boundary_count(text) == 1


def _contains_html_markup(text: str) -> bool:
    """Reject raw or entity-encoded HTML syntax, but not ordinary entities."""
    decoded = unescape(text)
    return (
        any(
            pattern.search(decoded)
            for pattern in (
                _HTML_TAG_RE,
                _HTML_DECLARATION_RE,
                _HTML_PROCESSING_INSTRUCTION_RE,
            )
        )
        or '<!--' in decoded
        or '-->' in decoded
        or '<?' in decoded
    )


def _contains_markdown_markup(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (
            _MARKDOWN_HEADING_RE,
            _MARKDOWN_LIST_RE,
            _MARKDOWN_LINK_RE,
            _MARKDOWN_REFERENCE_LINK_RE,
            _MARKDOWN_REFERENCE_DEFINITION_RE,
            _MARKDOWN_EMPHASIS_RE,
            _MARKDOWN_CODE_RE,
            _MARKDOWN_QUOTE_RE,
        )
    )


def _sentence_boundary_count(text: str) -> int:
    """Count sentence-ending runs while ignoring deterministic abbreviations."""
    count = 0
    index = 0
    while index < len(text):
        if text[index] not in _SENTENCE_TERMINATORS:
            index += 1
            continue

        end = index + 1
        while end < len(text) and text[end] in _SENTENCE_TERMINATORS:
            end += 1
        if _is_sentence_boundary(text, index, end):
            count += 1
            if count > 1:
                return count
        index = end
    if text[-1] in _TERMINAL_CLOSING_PUNCTUATION:
        before_closers = len(text) - 1
        while before_closers >= 0 and text[before_closers] in _CLOSING_PUNCTUATION:
            before_closers -= 1
        if before_closers < 0 or text[before_closers] not in _SENTENCE_TERMINATORS:
            count += 1
    return count


def _is_sentence_boundary(text: str, start: int, end: int) -> bool:
    """Return whether a punctuation run closes a sentence."""
    after_run = end
    while after_run < len(text) and text[after_run] in _CLOSING_PUNCTUATION:
        after_run += 1

    if after_run == len(text):
        return True
    if _run_is_abbreviation(text, start, end):
        return False
    return True


def _run_is_abbreviation(text: str, start: int, end: int) -> bool:
    """Recognize initials and common abbreviations without a broad regex."""
    if any(
        text[index] == '.'
        and index > 0
        and index + 1 < len(text)
        and text[index - 1].isdigit()
        and text[index + 1].isdigit()
        for index in range(start, end)
    ):
        return True

    token_start = start
    while token_start > 0 and (
        text[token_start - 1].isalnum() or text[token_start - 1] == '.'
    ):
        token_start -= 1
    token_end = end
    while token_end < len(text) and (
        text[token_end].isalnum() or text[token_end] == '.'
    ):
        token_end += 1
    token = text[token_start:token_end].lower()
    if token in _COMMON_ABBREVIATIONS:
        return True

    # Initialisms such as U.S. and U.S.A. have a letter followed by a period
    # at least twice.  The shape is checked procedurally to avoid swallowing
    # ordinary sentence punctuation or CJK text.
    components = token.split('.')
    return (
        token.endswith('.')
        and len(components) >= 3
        and all(
            component.isascii() and component.isalpha() for component in components[:-1]
        )
    )


__all__ = ['is_complete_plain_sentence']
