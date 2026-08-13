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


def test_validate_analysis_sections_returns_unavailable_for_top_or_nested_shape_errors() -> (
    None
):
    malformed_payloads = [
        None,
        {'sections': {}},
        _analysis_payload(sections=[{'kind': 'impact', 'title': '시장 영향'}]),
        _analysis_payload(sections=[_section(paragraphs={})]),
        _analysis_payload(sections=[_section(paragraphs=[None])]),
        _analysis_payload(sections=[_section(paragraphs=[{'sentences': {}}])]),
        _analysis_payload(
            sections=[_section(paragraphs=[_paragraph(sentences=[None])])]
        ),
    ]

    for payload in malformed_payloads:
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
    }


def test_validate_analysis_sections_rejects_malformed_section_alongside_valid_sibling() -> (
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
                                    'text': '유효 문장은 구조 오류 때문에 보존되지 않습니다.',
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
