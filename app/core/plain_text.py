"""Deterministic validation for public plain-text sentence fields."""

from __future__ import annotations

import re

_HTML_TAG_RE = re.compile(r'<\s*/?\s*[A-Za-z][^>]*>')
_MARKDOWN_HEADING_RE = re.compile(r'(?m)^\s{0,3}#{1,6}(?:\s|$)')
_MARKDOWN_LIST_RE = re.compile(r'(?m)^\s*(?:[-+*]|\d+[.)])\s+')
_MARKDOWN_LINK_RE = re.compile(r'!?\[[^\]]+\]\([^)]*\)')
_MARKDOWN_EMPHASIS_RE = re.compile(
    r'(?:\*\*|__|\*|_|~~)(?=\S)[^\r\n]+?(?<=\S)(?:\*\*|__|\*|_|~~)'
)
_MARKDOWN_CODE_RE = re.compile(r'`{1,3}')
_MARKDOWN_QUOTE_RE = re.compile(r'(?m)^\s*>\s?')
_SENTENCE_TERMINATOR_RE = re.compile(r'[.!?。！？…]+[\"\'”’»)]*(?=\s+[^.!?。！？…]|$)')
_SENTENCE_TERMINATORS = frozenset('.!?。！？…')
_CLOSING_PUNCTUATION = '"\'”’»)]}'


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
    if not text or '\n' in value or '\r' in value:
        return False
    if _HTML_TAG_RE.search(text):
        return False
    if any(
        pattern.search(text)
        for pattern in (
            _MARKDOWN_HEADING_RE,
            _MARKDOWN_LIST_RE,
            _MARKDOWN_LINK_RE,
            _MARKDOWN_EMPHASIS_RE,
            _MARKDOWN_CODE_RE,
            _MARKDOWN_QUOTE_RE,
        )
    ):
        return False
    sentence_text = text.rstrip(_CLOSING_PUNCTUATION)
    if not sentence_text or sentence_text[-1] not in _SENTENCE_TERMINATORS:
        return False
    if not any(
        char not in _SENTENCE_TERMINATORS and not char.isspace() for char in text
    ):
        return False
    return len(_SENTENCE_TERMINATOR_RE.findall(text)) == 1


__all__ = ['is_complete_plain_sentence']
