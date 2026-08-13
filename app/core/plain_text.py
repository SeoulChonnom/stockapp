"""Deterministic validation for public plain-text sentence fields."""

from __future__ import annotations

import re
from html import unescape

_ENTITY_DECODE_ROUNDS = 4
_MARKDOWN_HEADING_RE = re.compile(r'(?m)^\s{0,3}#{1,6}(?:\s|$)')
_MARKDOWN_LIST_RE = re.compile(r'(?m)^\s*(?:[-+*]|\d+[.)])\s+')
_MARKDOWN_LINK_RE = re.compile(r'!?\[[^\]\r\n]+\]\([^\)\r\n]*\)')
_MARKDOWN_REFERENCE_LINK_RE = re.compile(r'!?\[[^\]\r\n]+\]\[[^\]\r\n]*\]')
_MARKDOWN_REFERENCE_DEFINITION_RE = re.compile(r'(?m)^\s*\[[^\]\r\n]+\]:\s*\S+')
_MARKDOWN_EMPHASIS_RE = re.compile(
    r'(?<!\w)(?P<delimiter>\*\*|__|~~|\*|_)(?=\S)'
    r'[^\r\n]+?(?<=\S)(?P=delimiter)(?!\w)'
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
_CJK_PAIR_CLOSERS = {
    '「': '」',
    '『': '』',
    '《': '》',
    '〈': '〉',
    '【': '】',
    '〔': '〕',
    '（': '）',
    '［': '］',
    '〖': '〗',
    '〘': '〙',
    '〚': '〛',
    '〝': '〞',
    '“': '”',
    '‘': '’',
    '｢': '｣',
}
_CJK_PAIR_OPENERS = frozenset(_CJK_PAIR_CLOSERS)
_CJK_PAIR_CLOSER_SET = frozenset(_CJK_PAIR_CLOSERS.values())


def is_complete_plain_sentence(value: object) -> bool:
    """Return whether ``value`` is one nonblank, plain-text sentence.

    Validation uses a bounded, recursively decoded semantic view.  The input
    itself is never normalized or returned, so entity spelling remains stable
    on the public response while encoded syntax cannot bypass the contract.
    """
    if not isinstance(value, str):
        return False
    semantic = _decode_semantic_view(value)
    if any(character in _LINE_BREAKS for character in semantic):
        return False
    text = semantic.strip()
    if not text:
        return False
    if _contains_html_markup(text):
        return False
    if _contains_markdown_markup(text):
        return False
    if not _has_balanced_cjk_pairs(text):
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


def _decode_semantic_view(text: str) -> str:
    decoded = text
    for _ in range(_ENTITY_DECODE_ROUNDS):
        next_decoded = unescape(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    return decoded


def _contains_html_markup(text: str) -> bool:
    """Reject HTML syntax while allowing a plain comparison such as ``A<B>C``."""
    if '<!--' in text or '-->' in text or '<!' in text or '<?' in text:
        return True

    index = 0
    while index < len(text):
        opening = text.find('<', index)
        if opening == -1:
            return False
        if opening + 1 >= len(text):
            return True
        tag_end = _find_unquoted_tag_end(text, opening + 1)
        if tag_end is None:
            if text[opening + 1].isdigit():
                index = opening + 1
                continue
            return True
        body = text[opening + 1 : tag_end]
        if _is_safe_comparison(text, opening, tag_end, body):
            index = tag_end + 1
            continue
        if _parse_tag_name(body) is not None:
            return True
        index = tag_end + 1
    return False


def _find_unquoted_tag_end(text: str, start: int) -> int | None:
    quote: str | None = None
    for index in range(start, len(text)):
        character = text[index]
        if quote is not None:
            if character == quote:
                quote = None
        elif character in {'"', "'"}:
            quote = character
        elif character == '>':
            return index
    return None


def _is_safe_comparison(text: str, opening: int, tag_end: int, body: str) -> bool:
    content = body.strip()
    if (
        len(content) != 1
        or not content.isascii()
        or not content.isalpha()
        or opening == 0
        or tag_end + 1 >= len(text)
    ):
        return False
    before = text[opening - 1]
    after = text[tag_end + 1]
    return before.isascii() and before.isalnum() and after.isascii() and after.isalnum()


def _parse_tag_name(body: str) -> str | None:
    content = body.strip()
    if content.startswith('/'):
        content = content[1:].lstrip()
    if content.endswith('/'):
        content = content[:-1].rstrip()
    if not content or not (content[0].isascii() and content[0].isalpha()):
        return None
    index = 1
    while index < len(content) and (
        content[index].isascii()
        and (content[index].isalnum() or content[index] in {':', '-', '_'})
    ):
        index += 1
    remainder = content[index:]
    if remainder and not remainder.isspace():
        return content[:index]
    return content[:index]


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


def _has_balanced_cjk_pairs(text: str) -> bool:
    stack: list[str] = []
    for index, character in enumerate(text):
        if character in _CJK_PAIR_OPENERS:
            stack.append(character)
            continue
        if character not in _CJK_PAIR_CLOSER_SET:
            continue
        if stack and _CJK_PAIR_CLOSERS[stack[-1]] == character:
            stack.pop()
            continue
        if not stack and _is_terminal_unmatched_closer(text, index):
            continue
        return False
    return not stack


def _is_terminal_unmatched_closer(text: str, index: int) -> bool:
    if any(character not in _CLOSING_PUNCTUATION for character in text[index:]):
        return False
    return True


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
    token = text[token_start:token_end]
    if token.lower() in _COMMON_ABBREVIATIONS:
        return True

    components = token.split('.')
    return (
        token.endswith('.')
        and len(components) >= 3
        and all(
            len(component) == 1
            and component.isascii()
            and component.isalpha()
            and component.isupper()
            for component in components[:-1]
        )
    )


__all__ = ['is_complete_plain_sentence']
