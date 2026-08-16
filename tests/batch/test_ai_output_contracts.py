import pytest

from app.batch.ai_output_contracts import (
    ANALYSIS_ISSUE_MESSAGES,
    aggregate_conflict_status,
    build_unavailable_analysis,
    normalize_key_points,
    validate_analysis_sections,
)


def _key_points() -> list[dict[str, str]]:
    return [
        {
            'kind': 'direction',
            'label': '시장 방향',
            'text': '코스피가 상승했습니다.',
            'direction': 'UP',
        },
        {'kind': 'driver', 'label': '주요 원인', 'text': '반도체 강세가 이끌었습니다.'},
        {
            'kind': 'watch',
            'label': '관전 포인트',
            'text': '미국 물가를 확인해야 합니다.',
        },
    ]


def _analysis_payload(*, sections: list[object]) -> dict[str, object]:
    return {'sections': sections}


def _section(*, paragraphs: list[object]) -> dict[str, object]:
    return {'kind': 'impact', 'title': '시장 영향', 'paragraphs': paragraphs}


def _paragraph(*, sentences: list[object]) -> dict[str, object]:
    return {'sentences': sentences}


def _sentence(**overrides: object) -> dict[str, object]:
    sentence: dict[str, object] = {
        'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
        'sourceArticleIds': [1024],
        'conflictStatus': 'NONE',
        'conflictingSourceArticleIds': [],
        'conflictNote': None,
    }
    sentence.update(overrides)
    return sentence


def test_normalize_key_points_returns_the_three_contract_items() -> None:
    assert normalize_key_points(_key_points()) == {
        'keyPoints': [
            {
                'kind': 'direction',
                'label': '시장 방향',
                'text': '코스피가 상승했습니다.',
                'direction': 'UP',
            },
            {
                'kind': 'driver',
                'label': '주요 원인',
                'text': '반도체 강세가 이끌었습니다.',
            },
            {
                'kind': 'watch',
                'label': '관전 포인트',
                'text': '미국 물가를 확인해야 합니다.',
            },
        ]
    }


def test_normalize_key_points_rejects_semantic_contract_errors_as_one_fallback() -> (
    None
):
    invalid_payloads = [
        _key_points()[:2],
        [_key_points()[1], _key_points()[0], _key_points()[2]],
        [{**_key_points()[0], 'label': '방향'}] + _key_points()[1:],
        [{**_key_points()[0], 'text': '  '}] + _key_points()[1:],
        [{**_key_points()[0], 'direction': 'SIDEWAYS'}] + _key_points()[1:],
        [
            _key_points()[0],
            {**_key_points()[1], 'direction': 'UP'},
            _key_points()[2],
        ],
        [
            _key_points()[0],
            _key_points()[1],
            {**_key_points()[2], 'direction': 'UP'},
        ],
        [
            _key_points()[0],
            _key_points()[1],
            {**_key_points()[2], 'kind': 'other'},
        ],
        [
            _key_points()[0],
            _key_points()[1],
            {**_key_points()[2], 'kind': 'driver'},
        ],
        {'keyPoints': _key_points()},
    ]

    for payload in invalid_payloads:
        assert normalize_key_points(payload) == {
            'keyPoints': [],
            'issue': {
                'category': 'AI_SUMMARY',
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
            },
        }


@pytest.mark.parametrize(
    'text',
    [
        '   ',
        '첫 문장입니다. 둘째 문장입니다.',
        '줄바꿈이 포함된 문장입니다.\n',
        '캐리지 리턴이 포함된 문장입니다.\r',
        '<b>HTML 태그가 포함된 문장입니다.</b>',
        '# 제목 문장입니다.',
        '- 목록 항목입니다.',
        '1. 번호 목록 항목입니다.',
        '[문서 링크](https://example.com)입니다.',
        '**강조된 문장입니다.**',
        '`인라인 코드`가 포함된 문장입니다.',
        '문장에 마침표가 없습니다',
        '&lt;b&gt;인코딩된 태그&lt;/b&gt; 문장입니다.',
        '<!-- raw comment --> 문장입니다.',
        '<!DOCTYPE html> 문장입니다.',
        '<?xml version="1.0"?> 문장입니다.',
        '&lt;!-- 인코딩된 주석 --&gt; 문장입니다.',
        '[참고 문서][ref]를 확인했습니다.',
        '![차트 이미지](https://example.com/chart.png)를 확인했습니다.',
        '[ref]: https://example.com 문장입니다.',
        '문장입니다.\x85다음 문장입니다.',
        '문장입니다.\u2028다음 문장입니다.',
        '문장입니다.\u2029다음 문장입니다.',
        '.',
        '()!',
        '상승했습니다. 하락했습니다.',
    ],
    ids=[
        'blank',
        'multiple-sentences',
        'newline',
        'carriage-return',
        'html',
        'heading',
        'unordered-list',
        'ordered-list',
        'link',
        'emphasis',
        'code',
        'incomplete',
        'encoded-html',
        'comment',
        'declaration',
        'processing-instruction',
        'encoded-comment',
        'reference-link',
        'image-link',
        'reference-definition',
        'next-line',
        'line-separator',
        'paragraph-separator',
        'punctuation-only-period',
        'punctuation-only-parentheses',
        'two-sentences',
    ],
)
def test_normalize_key_points_rejects_non_plain_single_sentence_text(
    text: object,
) -> None:
    payload = _key_points()
    payload[0]['text'] = text

    assert normalize_key_points(payload) == {
        'keyPoints': [],
        'issue': {
            'category': 'AI_SUMMARY',
            'code': 'KEY_POINTS_GENERATION_FAILED',
            'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
        },
    }


@pytest.mark.parametrize(
    'text',
    [
        '미국 증시는 상승했지만 한국 증시는 하락했습니다.',
        '금리 인하 기대가 시장에 반영됐나요?',
        '반도체 업종이 강세를 보였습니다!',
        '실적 발표(예: 3.1조원)가 주가에 반영됐습니다.',
        '시장 흐름이 엇갈렸습니다."',
        '시장 흐름이 엇갈렸습니다。」',
        '시장 흐름이 엇갈렸습니다。』',
        '시장 흐름이 엇갈렸습니다》',
        '시장 흐름이 엇갈렸습니다〉',
        '시장 흐름이 엇갈렸습니다】',
        '시장 흐름이 엇갈렸습니다〕',
        '시장 흐름이 엇갈렸습니다）',
        '시장 흐름이 엇갈렸습니다］',
        '시장 흐름이 엇갈렸습니다〗',
        '시장 흐름이 엇갈렸습니다〙',
        '시장 흐름이 엇갈렸습니다〛',
        '시장 흐름이 엇갈렸습니다〞',
        '시장 흐름이 엇갈렸습니다〟',
        'U.S. 증시는 상승했습니다.',
        'U.S.A. 증시는 상승했습니다.',
        '손익분기점은 &lt;3%입니다.',
        '市場は上昇しました。',
        '한국 시장은 상승했습니다。',
    ],
    ids=[
        'korean-period',
        'question',
        'exclamation',
        'ordinary-punctuation',
        'closing-quote',
        'cjk-double-angle-quote',
        'cjk-closing-bracket',
        'cjk-closing-parenthesis',
        'cjk-square-bracket',
        'cjk-corner-bracket',
        'cjk-lenticular-bracket',
        'cjk-white-square-bracket',
        'cjk-double-square-bracket',
        'cjk-white-parenthesis',
        'cjk-closing-white-square',
        'cjk-double-angle-quote-right',
        'cjk-white-double-angle-quote',
        'cjk-double-prime',
        'us-abbreviation',
        'initialism',
        'encoded-comparison',
        'japanese-period',
        'cjk-period',
    ],
)
def test_normalize_key_points_accepts_plain_complete_single_sentences(
    text: str,
) -> None:
    payload = _key_points()
    payload[0]['text'] = text

    result = normalize_key_points(payload)

    assert result['keyPoints'][0]['text'] == text


@pytest.mark.parametrize(
    'text',
    [
        '문장입니다.&NewLine;다음 문장입니다.',
        '상승했습니다&period; 하락했습니다&period;',
        '&#91;ref&#93;&colon; https://example.com 문장입니다.',
        '&amp;lt;b&amp;gt;시장&amp;lt;/b&amp;gt; 문장입니다.',
        '*강조* 문장입니다.',
        '_강조_ 문장입니다.',
        '__강조__ 문장입니다.',
        '~~취소~~ 문장입니다.',
        '<b>시장</b> 문장입니다.',
        '<b>시장 문장입니다.',
        '<br> 문장입니다.',
        '<span class=x>시장 문장입니다.',
        '&lt;b&gt;시장&lt;/b&gt; 문장입니다.',
        '&lt;b&gt;시장 문장입니다.',
        '&lt;br&gt; 문장입니다.',
        '&lt;span class=x&gt;시장 문장입니다.',
        'Foo.Bar. 증시는 상승했습니다.',
        '첫 문장입니다」 둘째 문장입니다.',
        '「상승했습니다.',
    ],
    ids=[
        'encoded-newline',
        'encoded-period',
        'encoded-reference-definition',
        'double-encoded-tag',
        'single-emphasis',
        'underscore-emphasis',
        'double-underscore-emphasis',
        'strike-emphasis',
        'raw-paired-tag',
        'raw-start-tag',
        'raw-void-tag',
        'raw-attribute-tag',
        'encoded-paired-tag',
        'encoded-start-tag',
        'encoded-void-tag',
        'encoded-attribute-tag',
        'arbitrary-dotted-word',
        'intermediate-unmatched-closer',
        'unmatched-opener',
    ],
)
def test_normalize_key_points_rejects_encoded_markup_and_unbalanced_text(
    text: str,
) -> None:
    payload = _key_points()
    payload[0]['text'] = text

    assert normalize_key_points(payload) == {
        'keyPoints': [],
        'issue': {
            'category': 'AI_SUMMARY',
            'code': 'KEY_POINTS_GENERATION_FAILED',
            'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
        },
    }


@pytest.mark.parametrize(
    'text',
    [
        'AT&amp;T는 상승했습니다.',
        '상승_하락_혼조로 마감했습니다.',
        'A&lt;B&gt;C로 움직였습니다.',
        '「상승」 흐름이 이어졌습니다.',
        '「상승했습니다。」',
        '상승했습니다。）',
        '상승했습니다&period;',
    ],
    ids=[
        'safe-entity',
        'intraword-underscore',
        'encoded-comparison',
        'balanced-cjk-quote',
        'balanced-terminal-cjk-quote',
        'terminal-fullwidth-parenthesis',
        'encoded-terminal-period',
    ],
)
def test_normalize_key_points_accepts_decoded_plain_text_forms(text: str) -> None:
    payload = _key_points()
    payload[0]['text'] = text

    result = normalize_key_points(payload)

    assert result['keyPoints'][0]['text'] == text


def _nested_entity_encode(value: str, levels: int) -> str:
    for _ in range(levels):
        value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    return value


def test_normalize_key_points_rejects_markup_that_resolves_beyond_decode_cap() -> None:
    deeply_encoded_tag = _nested_entity_encode(
        '<b>시장</b> 문장입니다.',
        5,
    )
    payload = _key_points()
    payload[0]['text'] = deeply_encoded_tag

    assert normalize_key_points(payload)['issue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }


def test_normalize_key_points_keeps_safe_ampersand_entity() -> None:
    payload = _key_points()
    payload[0]['text'] = 'AT&amp;T는 상승했습니다.'

    assert normalize_key_points(payload)['keyPoints'][0]['text'] == (
        'AT&amp;T는 상승했습니다.'
    )


def test_aggregate_conflict_status_uses_found_then_not_checked_then_none() -> None:
    assert aggregate_conflict_status([]) == 'NOT_CHECKED'
    assert (
        aggregate_conflict_status(
            [
                {'conflictStatus': 'NONE'},
                {'conflictStatus': 'NOT_CHECKED'},
            ]
        )
        == 'NOT_CHECKED'
    )
    assert (
        aggregate_conflict_status(
            [
                {'conflictStatus': 'NOT_CHECKED'},
                {'conflictStatus': 'FOUND'},
                {'conflictStatus': 'NONE'},
            ]
        )
        == 'FOUND'
    )


def test_analysis_issue_messages_are_the_public_contract() -> None:
    assert ANALYSIS_ISSUE_MESSAGES == {
        'ANALYSIS_GENERATION_FAILED': '분석을 생성하지 못했습니다.',
        'NO_GROUNDED_SENTENCES': '근거를 확인할 수 있는 분석 문장이 없습니다.',
        'INVALID_SOURCE_REFERENCE': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
        'CONFLICT_CHECK_FAILED': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
    }


def test_build_unavailable_analysis_deduplicates_codes_in_discovery_order() -> None:
    assert build_unavailable_analysis(
        'INVALID_SOURCE_REFERENCE',
        'INVALID_SOURCE_REFERENCE',
        'NO_GROUNDED_SENTENCES',
    ) == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            },
            {
                'code': 'NO_GROUNDED_SENTENCES',
                'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
            },
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
    }


def test_build_unavailable_analysis_fails_closed_for_unknown_issue_codes() -> None:
    assert build_unavailable_analysis('UNKNOWN') == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
    }


@pytest.mark.parametrize(
    ('payload', 'expected_reason'),
    [
        pytest.param(None, 'payload_not_object', id='payload-not-object'),
        pytest.param({'sections': {}}, 'sections_not_list', id='sections-not-list'),
    ],
)
def test_validate_analysis_sections_names_the_rule_that_rejected_the_payload(
    payload: object, expected_reason: str
) -> None:
    """Only a payload with no readable sections at all is fatal.

    The public issue code is the same for both by design, so the reason is the
    only thing that says which rule fired -- without it an operator sees one
    ANALYSIS_GENERATION_FAILED and has nothing to act on.
    """
    assert validate_analysis_sections(payload, {1024}) == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
        'failureReason': expected_reason,
    }


@pytest.mark.parametrize(
    ('malformed_section', 'expected_reason'),
    [
        pytest.param(None, 'section_not_object', id='section-not-object'),
        pytest.param(
            {'kind': 'summary', 'title': '요약', 'paragraphs': []},
            'section_kind_unknown',
            id='unknown-kind',
        ),
        pytest.param(
            {'kind': 'background', 'title': '배경 설명', 'paragraphs': []},
            'section_title_mismatch',
            id='title-mismatch',
        ),
        pytest.param(
            {'kind': 'background', 'title': '발생 배경'},
            'section_paragraphs_not_list',
            id='section-without-paragraphs',
        ),
        pytest.param(
            {'kind': 'background', 'title': '발생 배경', 'paragraphs': {}},
            'section_paragraphs_not_list',
            id='paragraphs-not-list',
        ),
    ],
)
def test_validate_analysis_sections_drops_only_the_malformed_section(
    malformed_section: object, expected_reason: str
) -> None:
    """One unreadable section must not discard the sound ones beside it.

    The contract already lets the model omit a section it has nothing grounded
    to say in, so a section that cannot be read is dropped the same way. This
    is what production hit: a single section missing its paragraphs array threw
    away every other section in nineteen clusters.
    """
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                malformed_section,
                _section(paragraphs=[_paragraph(sentences=[_sentence()])]),
            ]
        ),
        {1024},
    )

    assert result['analysisStatus'] == 'READY'
    assert [section['kind'] for section in result['sections']] == ['impact']
    assert result['droppedSectionReasons'] == [expected_reason]


@pytest.mark.parametrize(
    ('malformed_paragraph', 'expected_reason'),
    [
        pytest.param(None, 'paragraph_not_object', id='paragraph-not-object'),
        pytest.param(
            {'sentences': {}}, 'paragraph_sentences_not_list', id='sentences-not-list'
        ),
    ],
)
def test_validate_analysis_sections_drops_only_the_malformed_paragraph(
    malformed_paragraph: object, expected_reason: str
) -> None:
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        malformed_paragraph,
                        _paragraph(sentences=[_sentence()]),
                    ]
                )
            ]
        ),
        {1024},
    )

    assert result['analysisStatus'] == 'READY'
    assert len(result['sections'][0]['paragraphs']) == 1
    assert result['droppedSectionReasons'] == [expected_reason]


def test_validate_analysis_sections_drops_only_the_sentence_that_is_not_an_object() -> (
    None
):
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[_paragraph(sentences=[None, _sentence()])],
                )
            ]
        ),
        {1024},
    )

    assert result['analysisStatus'] == 'READY'
    assert len(result['sections'][0]['paragraphs'][0]['sentences']) == 1
    assert result['droppedSectionReasons'] == ['sentence_not_object']


def test_validate_analysis_sections_imposes_the_fixed_order_and_drops_repeats() -> None:
    """The public order is ours to impose, not a reason to discard the work."""

    def _kind_section(kind: str, title: str, text: str) -> dict[str, object]:
        return {
            'kind': kind,
            'title': title,
            'paragraphs': [_paragraph(sentences=[_sentence(text=text)])],
        }

    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _kind_section('outlook', '향후 관전 포인트', '전망 문장입니다.'),
                _kind_section('background', '발생 배경', '배경 문장입니다.'),
                _kind_section('background', '발생 배경', '중복 배경 문장입니다.'),
            ]
        ),
        {1024},
    )

    assert result['analysisStatus'] == 'READY'
    assert [section['kind'] for section in result['sections']] == [
        'background',
        'outlook',
    ]
    assert result['droppedSectionReasons'] == ['section_kind_duplicated']


@pytest.mark.parametrize(
    'text',
    ['', '   ', '\n', '\r', None, 123, [], {}],
    ids=[
        'empty',
        'whitespace',
        'newline',
        'carriage-return',
        'null',
        'number',
        'list',
        'object',
    ],
)
def test_validate_analysis_sections_drops_only_the_sentence_missing_its_text(
    text: object,
) -> None:
    """One unusable sentence must not discard its sound siblings.

    Failing the whole analysis here threw away every grounded sentence in the
    cluster over a single empty one, which is the opposite of the isolation
    this function promises.
    """
    malformed_sentence = _sentence(text=text)
    payload = _analysis_payload(
        sections=[
            _section(
                paragraphs=[
                    _paragraph(sentences=[malformed_sentence]),
                    _paragraph(sentences=[_sentence(text='유효한 형제 문장입니다.')]),
                ]
            )
        ]
    )

    assert validate_analysis_sections(payload, {1024}) == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NONE',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '유효한 형제 문장입니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_rejects_invalid_section_contract() -> None:
    payloads = [
        _analysis_payload(sections=[{**_section(paragraphs=[]), 'kind': 'other'}]),
        _analysis_payload(sections=[{**_section(paragraphs=[]), 'title': '영향'}]),
        _analysis_payload(
            sections=[
                {'kind': 'related', 'title': '관련 업종·종목', 'paragraphs': []},
                _section(paragraphs=[]),
            ]
        ),
        _analysis_payload(sections=[_section(paragraphs=[]), _section(paragraphs=[])]),
    ]

    for payload in payloads:
        assert (
            validate_analysis_sections(payload, {1024})['analysisStatus']
            == 'UNAVAILABLE'
        )


def test_validate_analysis_sections_prunes_only_invalid_primary_source_sentence() -> (
    None
):
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(sourceArticleIds=[9999]),
                                _sentence(text='유효한 근거 문장은 남습니다.'),
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024},
    )

    assert result == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NONE',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '유효한 근거 문장은 남습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_rejects_all_invalid_primary_sources_with_causal_issues() -> (
    None
):
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(sourceArticleIds=[]),
                                _sentence(sourceArticleIds=[1024, 1024]),
                                _sentence(sourceArticleIds=[9999]),
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024},
    )

    assert result == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            },
            {
                'code': 'NO_GROUNDED_SENTENCES',
                'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
            },
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
        'failureReason': 'no_grounded_sentences',
    }


def test_validate_analysis_sections_degrades_invalid_conflict_without_losing_grounded_text() -> (
    None
):
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(
                                    conflictStatus='FOUND',
                                    conflictingSourceArticleIds=[],
                                    conflictNote='서술이 다릅니다.',
                                )
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024},
    )

    assert result == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NOT_CHECKED',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_degrades_each_invalid_conflict_variant() -> None:
    invalid_conflicts = [
        _sentence(
            conflictStatus='FOUND',
            conflictingSourceArticleIds=[9999],
            conflictNote='근거가 범위를 벗어났습니다.',
        ),
        _sentence(
            conflictStatus='FOUND',
            conflictingSourceArticleIds=[1024],
            conflictNote='동일 근거와 겹칩니다.',
        ),
        _sentence(
            conflictStatus='NONE',
            conflictingSourceArticleIds=[],
            conflictNote='NONE에는 메모가 없습니다.',
        ),
    ]

    for invalid_conflict in invalid_conflicts:
        result = validate_analysis_sections(
            _analysis_payload(
                sections=[
                    _section(paragraphs=[_paragraph(sentences=[invalid_conflict])])
                ]
            ),
            {1024, 1025},
        )
        assert result['analysisStatus'] == 'PARTIAL'
        assert result['analysisIssues'] == [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ]
        sentence = result['sections'][0]['paragraphs'][0]['sentences'][0]
        assert sentence['text'] == '반도체 업종 약세가 지수에 부담을 줬습니다.'
        assert sentence['conflictStatus'] == 'NOT_CHECKED'


def test_validate_analysis_sections_keeps_valid_found_conflict_ready() -> None:
    assert (
        validate_analysis_sections(
            _analysis_payload(
                sections=[
                    _section(
                        paragraphs=[
                            _paragraph(
                                sentences=[
                                    _sentence(
                                        conflictStatus='FOUND',
                                        conflictingSourceArticleIds=[1025],
                                        conflictNote='다른 기사와 해석이 다릅니다.',
                                    )
                                ]
                            )
                        ]
                    )
                ]
            ),
            {1024, 1025},
        )['analysisStatus']
        == 'READY'
    )


def test_validate_analysis_sections_retains_not_checked_as_partial() -> None:
    assert validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(
                                    conflictStatus='NOT_CHECKED',
                                    conflictingSourceArticleIds=[],
                                    conflictNote=None,
                                )
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024},
    ) == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NOT_CHECKED',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_deduplicates_repeated_not_checked_issue() -> None:
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(
                                    text='첫 번째 충돌 미확인 문장입니다.',
                                    conflictStatus='NOT_CHECKED',
                                ),
                                _sentence(
                                    text='두 번째 충돌 미확인 문장입니다.',
                                    conflictStatus='NOT_CHECKED',
                                ),
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024},
    )

    assert result['analysisStatus'] == 'PARTIAL'
    assert result['analysisIssues'] == [
        {
            'code': 'CONFLICT_CHECK_FAILED',
            'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
        }
    ]
    assert result['conflictStatus'] == 'NOT_CHECKED'
    assert len(result['sections'][0]['paragraphs'][0]['sentences']) == 2


def test_validate_analysis_sections_found_and_not_checked_is_partial_found() -> None:
    result = validate_analysis_sections(
        _analysis_payload(
            sections=[
                _section(
                    paragraphs=[
                        _paragraph(
                            sentences=[
                                _sentence(
                                    text='보도 간 충돌이 확인됐습니다.',
                                    conflictStatus='FOUND',
                                    conflictingSourceArticleIds=[1025],
                                    conflictNote='기사별 수급 방향이 다릅니다.',
                                ),
                                _sentence(
                                    text='다른 영향은 충돌을 확인하지 못했습니다.',
                                    conflictStatus='NOT_CHECKED',
                                ),
                            ]
                        )
                    ]
                )
            ]
        ),
        {1024, 1025},
    )

    assert result == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'FOUND',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '보도 간 충돌이 확인됐습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'FOUND',
                                'conflictingSourceArticleIds': [1025],
                                'conflictNote': '기사별 수급 방향이 다릅니다.',
                            },
                            {
                                'text': '다른 영향은 충돌을 확인하지 못했습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NOT_CHECKED',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            },
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_omits_empty_input_containers_without_degrading() -> (
    None
):
    assert validate_analysis_sections(
        _analysis_payload(
            sections=[
                {'kind': 'background', 'title': '발생 배경', 'paragraphs': []},
                _section(paragraphs=[_paragraph(sentences=[_sentence()])]),
            ]
        ),
        {1024},
    ) == {
        'analysisStatus': 'READY',
        'analysisIssues': [],
        'conflictStatus': 'NONE',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_normalize_key_points_falls_back_for_unhashable_direction_json() -> None:
    invalid_payloads = [
        [
            {
                'kind': 'direction',
                'label': '시장 방향',
                'text': '코스피가 상승했습니다.',
                'direction': ['UP'],
            },
            {
                'kind': 'driver',
                'label': '주요 원인',
                'text': '반도체 강세가 이끌었습니다.',
            },
            {
                'kind': 'watch',
                'label': '관전 포인트',
                'text': '미국 물가를 확인해야 합니다.',
            },
        ],
        [
            {
                'kind': 'direction',
                'label': '시장 방향',
                'text': '코스피가 상승했습니다.',
                'direction': {'value': 'UP'},
            },
            {
                'kind': 'driver',
                'label': '주요 원인',
                'text': '반도체 강세가 이끌었습니다.',
            },
            {
                'kind': 'watch',
                'label': '관전 포인트',
                'text': '미국 물가를 확인해야 합니다.',
            },
        ],
    ]

    for payload in invalid_payloads:
        assert normalize_key_points(payload) == {
            'keyPoints': [],
            'issue': {
                'category': 'AI_SUMMARY',
                'code': 'KEY_POINTS_GENERATION_FAILED',
                'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
            },
        }


def test_validate_analysis_sections_returns_unavailable_for_empty_analysis() -> None:
    assert validate_analysis_sections({'sections': []}, {1024}) == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'NO_GROUNDED_SENTENCES',
                'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
        'failureReason': 'no_grounded_sentences',
    }


def test_validate_analysis_sections_keeps_the_valid_sibling_of_a_malformed_section() -> (
    None
):
    assert validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'impact',
                    'title': '시장 영향',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '유효 문장은 형제의 구조 오류에도 보존됩니다.',
                                    'sourceArticleIds': [1024],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                }
                            ]
                        }
                    ],
                },
                None,
            ]
        },
        {1024},
    ) == {
        'analysisStatus': 'READY',
        'analysisIssues': [],
        'conflictStatus': 'NONE',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '유효 문장은 형제의 구조 오류에도 보존됩니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
        'droppedSectionReasons': ['section_not_object'],
    }


def test_validate_analysis_sections_degrades_missing_or_unhashable_conflict_fields() -> (
    None
):
    invalid_sentences = [
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'NONE',
            'conflictingSourceArticleIds': [],
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': ['NONE'],
            'conflictingSourceArticleIds': [],
            'conflictNote': None,
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictingSourceArticleIds': [],
            'conflictNote': None,
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'NONE',
            'conflictNote': None,
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [True],
            'conflictNote': '불린 ID입니다.',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': ['1025'],
            'conflictNote': '문자열 ID입니다.',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [1025, 1025],
            'conflictNote': '중복 ID입니다.',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [9999],
            'conflictNote': '범위를 벗어난 ID입니다.',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [1024],
            'conflictNote': 'primary와 겹칩니다.',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [1025],
            'conflictNote': '   ',
        },
        {
            'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': {'value': 'NONE'},
            'conflictingSourceArticleIds': [],
            'conflictNote': None,
        },
    ]

    for invalid_sentence in invalid_sentences:
        assert validate_analysis_sections(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': [{'sentences': [invalid_sentence]}],
                    }
                ]
            },
            {1024},
        ) == {
            'analysisStatus': 'PARTIAL',
            'analysisIssues': [
                {
                    'code': 'CONFLICT_CHECK_FAILED',
                    'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
                }
            ],
            'conflictStatus': 'NOT_CHECKED',
            'sections': [
                {
                    'kind': 'impact',
                    'title': '시장 영향',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '충돌 검증 실패여도 근거 문장은 남습니다.',
                                    'sourceArticleIds': [1024],
                                    'conflictStatus': 'NOT_CHECKED',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                }
                            ]
                        }
                    ],
                }
            ],
        }


def test_validate_analysis_sections_reports_mixed_causal_issues_once_in_discovery_order() -> (
    None
):
    assert validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'impact',
                    'title': '시장 영향',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '첫 문장은 근거가 없습니다.',
                                    'sourceArticleIds': [],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                },
                                {
                                    'text': '둘째 문장은 충돌 필드가 잘못됐습니다.',
                                    'sourceArticleIds': [1024],
                                    'conflictStatus': 'FOUND',
                                    'conflictingSourceArticleIds': [1024, 1024],
                                    'conflictNote': '중복입니다.',
                                },
                                {
                                    'text': '셋째 문장도 근거가 없습니다.',
                                    'sourceArticleIds': [9999],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                },
                            ]
                        }
                    ],
                }
            ]
        },
        {1024},
    ) == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            },
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            },
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '둘째 문장은 충돌 필드가 잘못됐습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NOT_CHECKED',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_prunes_boolean_and_non_integer_primary_ids() -> (
    None
):
    assert validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'impact',
                    'title': '시장 영향',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '불린 ID는 근거가 아닙니다.',
                                    'sourceArticleIds': [True],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                },
                                {
                                    'text': '문자열 ID는 근거가 아닙니다.',
                                    'sourceArticleIds': ['1024'],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                },
                                {
                                    'text': '정수 ID 문장은 남습니다.',
                                    'sourceArticleIds': [1024],
                                    'conflictStatus': 'NONE',
                                    'conflictingSourceArticleIds': [],
                                    'conflictNote': None,
                                },
                            ]
                        }
                    ],
                }
            ]
        },
        {1024},
    ) == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NONE',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '정수 ID 문장은 남습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
    }


def test_validate_analysis_sections_keeps_valid_found_with_found_aggregate() -> None:
    assert validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'impact',
                    'title': '시장 영향',
                    'paragraphs': [
                        {
                            'sentences': [
                                {
                                    'text': '서로 다른 보도가 있어 충돌로 확인됐습니다.',
                                    'sourceArticleIds': [1024],
                                    'conflictStatus': 'FOUND',
                                    'conflictingSourceArticleIds': [1025],
                                    'conflictNote': '기사별 수치 해석이 다릅니다.',
                                }
                            ]
                        }
                    ],
                }
            ]
        },
        {1024, 1025},
    ) == {
        'analysisStatus': 'READY',
        'analysisIssues': [],
        'conflictStatus': 'FOUND',
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '서로 다른 보도가 있어 충돌로 확인됐습니다.',
                                'sourceArticleIds': [1024],
                                'conflictStatus': 'FOUND',
                                'conflictingSourceArticleIds': [1025],
                                'conflictNote': '기사별 수치 해석이 다릅니다.',
                            }
                        ]
                    }
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    'title',
    [
        '관련 업종ㆍ종목',
        '관련 업종・종목',
        '관련 업종·종목',
        '관련 업종 · 종목',
        '관련업종/종목',
    ],
    ids=['hangul-dot', 'katakana-dot', 'greek-ano-teleia', 'spaced', 'slash'],
)
def test_validate_analysis_sections_accepts_separator_variants_in_a_fixed_title(
    title: str,
) -> None:
    """A separator no reader can tell apart must not discard the analysis.

    The contract fixes the title, and the model is told exactly what it is, but
    demanding one particular codepoint threw away a whole cluster's analysis
    over a character that renders the same.
    """
    result = validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'related',
                    'title': title,
                    'paragraphs': [_paragraph(sentences=[_sentence()])],
                }
            ]
        },
        {1024},
    )

    assert result['analysisStatus'] == 'READY'
    # The canonical spelling is what gets stored, because the public schema
    # still requires an exact match on the persisted row.
    assert result['sections'][0]['title'] == '관련 업종·종목'


def test_validate_analysis_sections_still_rejects_a_different_title() -> None:
    """Folding punctuation must not turn the title check into no check."""
    result = validate_analysis_sections(
        {
            'sections': [
                {
                    'kind': 'related',
                    'title': '관련 종목 정리',
                    'paragraphs': [_paragraph(sentences=[_sentence()])],
                }
            ]
        },
        {1024},
    )

    # Nothing else was in the payload, so dropping the section leaves nothing
    # to display -- but the drop is still recorded as a title mismatch rather
    # than being indistinguishable from a model that wrote no analysis.
    assert result['analysisStatus'] == 'UNAVAILABLE'
    assert result['failureReason'] == 'all_sections_dropped'
    assert result['droppedSectionReasons'] == ['section_title_mismatch']
