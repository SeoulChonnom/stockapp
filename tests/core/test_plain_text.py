from __future__ import annotations

import pytest

from app.core.plain_text import is_complete_plain_sentence, plain_sentence_defect


@pytest.mark.parametrize(
    ('text', 'expected_defect'),
    [
        (None, 'text_not_string'),
        (
            '&amp;amp;amp;amp;lt;b&amp;amp;amp;amp;gt; 문장입니다.',
            'text_unresolved_entity',
        ),
        ('첫 줄입니다.\n둘째 줄입니다.', 'text_line_break'),
        ('   ', 'text_blank'),
        ('<b>강조된 문장입니다.</b>', 'text_html_markup'),
        ('# 제목 문장입니다.', 'text_markdown_markup'),
        ('「상승했습니다.', 'text_unbalanced_pair'),
        ('()!', 'text_no_alphanumeric'),
        ('마침표가 없는 문장입니다', 'text_no_terminator'),
        ('올랐습니다. 내렸습니다.', 'text_multiple_sentences'),
        ('코스피가 2.5% 오른 3,120.45로 마감했습니다.', None),
        ('AI·반도체 업종이 지수 상승을 이끌었습니다.', None),
        ('“관세 유예” 발언이 투자심리를 돌려세웠습니다.', None),
        ('U.S. 고용지표가 예상을 웃돌았습니다.', None),
    ],
    ids=[
        'not-string',
        'unresolved-entity',
        'line-break',
        'blank',
        'html',
        'markdown',
        'unbalanced-pair',
        'no-alphanumeric',
        'no-terminator',
        'multiple-sentences',
        'decimal-and-thousands',
        'middle-dot',
        'balanced-quotes',
        'known-abbreviation',
    ],
)
def test_plain_sentence_defect_names_the_rule_it_broke(
    text: object, expected_defect: str | None
) -> None:
    """Nine unrelated rules used to leave through one ``False``.

    A caller that records a rejection can now say which rule to go and fix:
    a second sentence and an unbalanced quote call for different prompts.
    """
    assert plain_sentence_defect(text) == expected_defect


@pytest.mark.parametrize(
    'text',
    [
        None,
        '   ',
        '올랐습니다. 내렸습니다.',
        '마침표가 없는 문장입니다',
        '코스피가 2.5% 오른 3,120.45로 마감했습니다.',
        'AI·반도체 업종이 지수 상승을 이끌었습니다.',
    ],
)
def test_is_complete_plain_sentence_agrees_with_the_named_defect(text: object) -> None:
    """The boolean is the same contract, so the two must never disagree."""
    assert is_complete_plain_sentence(text) == (plain_sentence_defect(text) is None)
