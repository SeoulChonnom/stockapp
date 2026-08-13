from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime

import pytest  # pyright: ignore[reportMissingImports]
from pydantic import ValidationError

from tests.support import jsonable, load_module

clusters_assembler_module = load_module('app.domains.clusters.assembler')

assemble_cluster_detail_response = (
    clusters_assembler_module.assemble_cluster_detail_response
)
build_cluster_detail_payload = clusters_assembler_module.build_cluster_detail_payload


ANALYSIS_GENERATED_AT = '2026-08-13T07:20:00+09:00'
SECTIONS = [
    {
        'kind': 'background',
        'title': '발생 배경',
        'paragraphs': [
            {
                'sentences': [
                    {
                        'text': '미국 반도체주 약세가 국내 시장으로 이어졌습니다.',
                        'sourceArticleIds': [2001],
                        'conflictStatus': 'NONE',
                        'conflictingSourceArticleIds': [],
                        'conflictNote': None,
                    }
                ]
            }
        ],
    },
    {
        'kind': 'impact',
        'title': '시장 영향',
        'paragraphs': [
            {
                'sentences': [
                    {
                        'text': '반도체 업종 약세가 지수에 부담을 줬습니다.',
                        'sourceArticleIds': [2002],
                        'conflictStatus': 'NONE',
                        'conflictingSourceArticleIds': [],
                        'conflictNote': None,
                    }
                ]
            }
        ],
    },
    {
        'kind': 'related',
        'title': '관련 업종·종목',
        'paragraphs': [
            {
                'sentences': [
                    {
                        'text': '관련 업종도 함께 움직였습니다.',
                        'sourceArticleIds': [2003],
                        'conflictStatus': 'NONE',
                        'conflictingSourceArticleIds': [],
                        'conflictNote': None,
                    }
                ]
            }
        ],
    },
    {
        'kind': 'outlook',
        'title': '향후 관전 포인트',
        'paragraphs': [
            {
                'sentences': [
                    {
                        'text': '다음 거래일 수급을 확인해야 합니다.',
                        'sourceArticleIds': [2001],
                        'conflictStatus': 'NONE',
                        'conflictingSourceArticleIds': [],
                        'conflictNote': None,
                    }
                ]
            }
        ],
    },
]


def _summary_record(*, paragraphs, metadata, status='SUCCESS', fallback_used=False):
    from app.db.repositories.projections import AiSummaryRecord

    return AiSummaryRecord(
        summary_id=901,
        batch_job_id=801,
        summary_type='CLUSTER_DETAIL_ANALYSIS',
        business_date=datetime(2026, 3, 17, tzinfo=UTC).date(),
        market_type='US',
        cluster_id=7001,
        title='저장 분석',
        body='저장 분석 본문',
        paragraphs_json=paragraphs,
        model_name='test-model',
        prompt_version='test-prompt',
        status=status,
        fallback_used=fallback_used,
        error_message=None,
        metadata_json=metadata,
        generated_at=datetime(2026, 3, 18, 5, 0, tzinfo=UTC),
    )


def _grounded_sections(
    *, conflict_status='NONE', conflict_ids=None, conflict_note=None
):
    return [
        {
            'kind': 'background',
            'title': '발생 배경',
            'paragraphs': [
                {
                    'sentences': [
                        {
                            'text': '반도체 업종 강세가 국내 시장으로 이어졌습니다.',
                            'sourceArticleIds': [4001],
                            'conflictStatus': conflict_status,
                            'conflictingSourceArticleIds': conflict_ids or [],
                            'conflictNote': conflict_note,
                        }
                    ]
                }
            ],
        }
    ]


def _structured_cluster_payload(sample_cluster_detail_payload):
    articles = [
        {
            **article,
            'processedArticleId': 2001 + index,
        }
        for index, article in enumerate(sample_cluster_detail_payload['articles'])
    ]
    cluster_id = sample_cluster_detail_payload['clusterId']
    grouped_articles = [
        {
            **article,
            'similarGroupId': f'sim-{cluster_id}-{index}',
            'isSimilarGroupRepresentative': True,
            'exactDuplicateCount': 0,
        }
        for index, article in enumerate(articles, start=1)
    ]
    return {
        **sample_cluster_detail_payload,
        'articleCount': len(grouped_articles),
        'summary': {
            'short': '반도체 업종 강세가 나스닥 상승을 견인했다.',
            'long': '외국인 매도와 업황 우려가 함께 반영됐습니다.',
            'analysisStatus': 'READY',
            'analysisGeneratedAt': ANALYSIS_GENERATED_AT,
            'analysisIssues': [],
            'conflictStatus': 'NONE',
            'sections': deepcopy(SECTIONS),
        },
        'representativeArticle': {
            **sample_cluster_detail_payload['representativeArticle'],
            'processedArticleId': 2001,
            'similarGroupId': f'sim-{cluster_id}-1',
            'isSimilarGroupRepresentative': True,
            'exactDuplicateCount': 0,
        },
        'articles': grouped_articles,
        'articleGrouping': {
            'status': 'UNAVAILABLE',
            'generatedAt': None,
            'issue': {
                'code': 'SIMILARITY_GROUPING_FAILED',
                'message': '유사 기사 묶음을 생성하지 못했습니다.',
            },
        },
    }


def test_cluster_assembler_returns_structured_analysis_contract(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)

    response = jsonable(assemble_cluster_detail_response(payload))

    assert 'analysis' not in response['summary']
    assert response['summary'] == {
        **payload['summary'],
        'analysisGeneratedAt': '2026-08-12T22:20:00Z',
    }


@pytest.mark.parametrize(
    'field',
    [
        'analysisStatus',
        'analysisGeneratedAt',
        'analysisIssues',
        'conflictStatus',
        'sections',
    ],
)
def test_cluster_summary_requires_structured_analysis_fields(
    field,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    del payload['summary'][field]

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'field,value',
    [
        ('analysisStatus', 'FAILED'),
        ('conflictStatus', 'UNKNOWN'),
        ('analysisIssues', [{'code': 'UNKNOWN', 'message': '잘못된 코드'}]),
    ],
    ids=['analysis-status', 'conflict-status', 'analysis-issue-code'],
)
def test_cluster_summary_rejects_unapproved_analysis_enums(
    field,
    value,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary'][field] = value

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'sections',
    [
        [SECTIONS[1], SECTIONS[0]],
        [{**SECTIONS[0], 'title': '잘못된 제목'}],
        [SECTIONS[0], SECTIONS[0]],
    ],
    ids=['wrong-order', 'wrong-title', 'duplicate-kind'],
)
def test_cluster_summary_rejects_invalid_section_structure(
    sections,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['sections'] = sections

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_cluster_summary_rejects_unapproved_section_kind(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['sections'][0]['kind'] = 'summary'

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'text',
    ['', '   ', None, 123, []],
    ids=['empty', 'whitespace', 'null', 'number', 'list'],
)
def test_analysis_sentence_rejects_malformed_text(
    text,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0]['text'] = text

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'sections',
    [
        [{**SECTIONS[0], 'paragraphs': []}],
        [{**SECTIONS[0], 'paragraphs': [{'sentences': []}]}],
    ],
    ids=['empty-paragraphs', 'empty-sentences'],
)
def test_analysis_section_rejects_empty_nested_containers(
    sections,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['sections'] = sections

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'summary_updates',
    [
        {
            'analysisStatus': 'READY',
            'analysisIssues': [
                {
                    'code': 'NO_GROUNDED_SENTENCES',
                    'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
                }
            ],
        },
        {'analysisStatus': 'PARTIAL', 'analysisIssues': []},
        {'analysisStatus': 'READY', 'sections': []},
        {'analysisStatus': 'PARTIAL', 'sections': []},
    ],
    ids=[
        'ready-with-issues',
        'partial-without-issues',
        'ready-empty-sections',
        'partial-empty-sections',
    ],
)
def test_cluster_summary_requires_status_specific_content(
    summary_updates,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary'].update(summary_updates)

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'conflict_status',
    ['NOT_CHECKED', 'FOUND'],
    ids=['not-checked', 'aggregate-mismatch'],
)
def test_ready_analysis_requires_completed_consistent_conflict_aggregate(
    conflict_status,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    if conflict_status == 'FOUND':
        sentence = payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0]
        sentence.update(
            {
                'conflictStatus': 'FOUND',
                'conflictingSourceArticleIds': [2002],
                'conflictNote': '기사별 수급 방향이 다르게 보도됐습니다.',
            }
        )
        payload['summary']['conflictStatus'] = 'NONE'
    else:
        payload['summary']['conflictStatus'] = conflict_status

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_analysis_issue_message_must_match_approved_code(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary'].update(
        {
            'analysisStatus': 'PARTIAL',
            'analysisIssues': [
                {
                    'code': 'CONFLICT_CHECK_FAILED',
                    'message': '저장된 임의 메시지입니다.',
                }
            ],
        }
    )

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'source_ids',
    [[], [2001, 2001], [True], ['2001'], [2001.0]],
    ids=['empty', 'duplicate', 'bool', 'numeric-string', 'float'],
)
def test_analysis_sentence_rejects_invalid_primary_source_cardinality(
    source_ids,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0][
        'sourceArticleIds'
    ] = source_ids

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'conflict_fields',
    [
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [],
            'conflictNote': '기사별 보도가 다릅니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [2002, 2002],
            'conflictNote': '기사별 보도가 다릅니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [2001],
            'conflictNote': '기사별 보도가 다릅니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [2002],
            'conflictNote': '   ',
        },
        {
            'conflictStatus': 'NONE',
            'conflictingSourceArticleIds': [2002],
            'conflictNote': None,
        },
        {
            'conflictStatus': 'NOT_CHECKED',
            'conflictingSourceArticleIds': [],
            'conflictNote': '완료되지 않았습니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [True],
            'conflictNote': '불린 ID입니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': ['2002'],
            'conflictNote': '문자열 ID입니다.',
        },
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [2002.0],
            'conflictNote': '실수 ID입니다.',
        },
    ],
    ids=[
        'found-empty-ids',
        'found-duplicate-ids',
        'overlapping-ids',
        'found-blank-note',
        'none-with-ids',
        'not-checked-with-note',
        'found-bool-id',
        'found-string-id',
        'found-float-id',
    ],
)
def test_analysis_sentence_rejects_invalid_conflict_cardinality(
    conflict_fields,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    sentence = payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0]
    sentence.update(conflict_fields)

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_analysis_sentence_accepts_valid_found_conflict(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary']['conflictStatus'] = 'FOUND'
    sentence = payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0]
    sentence.update(
        {
            'conflictStatus': 'FOUND',
            'conflictingSourceArticleIds': [2002],
            'conflictNote': '기사별 외국인 순매매 방향이 다르게 보도됐습니다.',
        }
    )

    response = jsonable(assemble_cluster_detail_response(payload))

    assert response['summary']['sections'][0]['paragraphs'][0]['sentences'][0] == {
        **sentence,
    }


def test_cluster_summary_accepts_truthful_unavailable_state(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary'].update(
        {
            'analysisStatus': 'UNAVAILABLE',
            'analysisGeneratedAt': None,
            'conflictStatus': 'NOT_CHECKED',
            'sections': [],
        }
    )

    response = jsonable(assemble_cluster_detail_response(payload))

    assert response['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert response['summary']['analysisGeneratedAt'] is None
    assert response['summary']['conflictStatus'] == 'NOT_CHECKED'
    assert response['summary']['sections'] == []


@pytest.mark.parametrize(
    'summary_updates',
    [
        {'sections': SECTIONS},
        {'analysisGeneratedAt': ANALYSIS_GENERATED_AT},
        {'conflictStatus': 'NONE'},
    ],
    ids=['sections-present', 'generated-at-present', 'aggregate-not-checked'],
)
def test_unavailable_analysis_requires_empty_truthful_state(
    summary_updates,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['summary'].update(
        {
            'analysisStatus': 'UNAVAILABLE',
            'analysisGeneratedAt': None,
            'conflictStatus': 'NOT_CHECKED',
            'sections': [],
            **summary_updates,
        }
    )

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize('article_key', ['representativeArticle', 'articles'])
@pytest.mark.parametrize(
    'processed_article_id',
    ['missing', None, True, '2001', 2001.0],
    ids=['missing', 'null', 'bool', 'numeric-string', 'float'],
)
def test_cluster_articles_require_integer_processed_article_id(
    article_key,
    processed_article_id,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    article = (
        payload[article_key]
        if article_key == 'representativeArticle'
        else payload[article_key][0]
    )
    if processed_article_id == 'missing':
        del article['processedArticleId']
    else:
        article['processedArticleId'] = processed_article_id

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_cluster_grouping_placeholders_are_explicitly_unavailable(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)

    response = jsonable(assemble_cluster_detail_response(payload))

    assert response['articleGrouping'] == {
        'status': 'UNAVAILABLE',
        'generatedAt': None,
        'issue': {
            'code': 'SIMILARITY_GROUPING_FAILED',
            'message': '유사 기사 묶음을 생성하지 못했습니다.',
        },
    }
    for article in [response['representativeArticle'], *response['articles']]:
        assert article['similarGroupId'].startswith(f'sim-{response["clusterId"]}-')
        assert article['isSimilarGroupRepresentative'] is True
        assert article['exactDuplicateCount'] == 0


def test_cluster_builder_emits_unavailable_singleton_grouping(
    sample_cluster_row,
    sample_processed_article_rows,
):
    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
    )

    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisGeneratedAt'] is None
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }
    ]
    assert payload['articleGrouping'] == {
        'status': 'UNAVAILABLE',
        'generatedAt': None,
        'issue': {
            'code': 'SIMILARITY_GROUPING_FAILED',
            'message': '유사 기사 묶음을 생성하지 못했습니다.',
        },
    }
    assert [article['processedArticleId'] for article in payload['articles']] == [
        4001,
        4002,
        4003,
    ]
    assert [article['similarGroupId'] for article in payload['articles']] == [
        f'sim-{sample_cluster_row["cluster_uid"]}-1',
        f'sim-{sample_cluster_row["cluster_uid"]}-2',
        f'sim-{sample_cluster_row["cluster_uid"]}-3',
    ]
    assert all(
        article['isSimilarGroupRepresentative'] for article in payload['articles']
    )
    assert all(article['exactDuplicateCount'] == 0 for article in payload['articles'])


def test_cluster_builder_reads_persisted_sections_and_generated_at(
    sample_cluster_row,
    sample_processed_article_rows,
):
    persisted_summary = _summary_record(
        paragraphs=[
            {
                'kind': 'background',
                'title': '발생 배경',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '반도체 업종 강세가 국내 시장으로 이어졌습니다.',
                                'sourceArticleIds': [4001],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
        metadata={
            'analysisStatus': 'READY',
            'analysisIssues': [],
            'conflictStatus': 'NONE',
        },
    )

    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        persisted_summary,
    )

    assert payload['summary']['short'] == sample_cluster_row['summary_short']
    assert payload['summary']['long'] == sample_cluster_row['summary_long']
    assert payload['summary']['analysisStatus'] == 'READY'
    assert payload['summary']['analysisGeneratedAt'] == '2026-03-18T05:00:00Z'
    assert payload['summary']['sections'][0]['paragraphs'][0]['sentences'][0][
        'sourceArticleIds'
    ] == [4001]


def test_cluster_builder_degrades_persisted_unknown_source_ids(
    sample_cluster_row,
    sample_processed_article_rows,
):
    persisted_summary = _summary_record(
        paragraphs=[
            {
                'kind': 'background',
                'title': '발생 배경',
                'paragraphs': [
                    {
                        'sentences': [
                            {
                                'text': '응답 기사에 없는 근거를 참조합니다.',
                                'sourceArticleIds': [9999],
                                'conflictStatus': 'NONE',
                                'conflictingSourceArticleIds': [],
                                'conflictNote': None,
                            }
                        ]
                    }
                ],
            }
        ],
        metadata={
            'analysisStatus': 'PARTIAL',
            'analysisIssues': [
                {
                    'code': 'INVALID_SOURCE_REFERENCE',
                    'message': '저장된 메시지는 사용하지 않습니다.',
                }
            ],
            'conflictStatus': 'NOT_CHECKED',
        },
    )

    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        persisted_summary,
    )

    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisGeneratedAt'] is None
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'INVALID_SOURCE_REFERENCE',
            'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
        },
        {
            'code': 'NO_GROUNDED_SENTENCES',
            'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
        },
    ]
    assert payload['summary']['sections'] == []


def test_cluster_builder_rejects_missing_processed_article_id(
    sample_cluster_row,
    sample_processed_article_rows,
):
    invalid_article = {**sample_processed_article_rows[0], 'id': None}

    with pytest.raises(ValueError, match='processed article id'):
        build_cluster_detail_payload(
            sample_cluster_row,
            invalid_article,
            [invalid_article, *sample_processed_article_rows[1:]],
        )


@pytest.mark.parametrize(
    'metadata',
    [
        None,
        {
            'analysisStatus': 'READY',
            'analysisIssues': 'not-a-list',
            'conflictStatus': 'NONE',
        },
        {
            'analysisStatus': 'BROKEN',
            'analysisIssues': [],
            'conflictStatus': 'NONE',
        },
        {
            'analysisStatus': ['READY'],
            'analysisIssues': [],
            'conflictStatus': 'NONE',
        },
        {
            'analysisStatus': 'READY',
            'analysisIssues': [
                {'code': 'UNKNOWN', 'message': 'provider secret'},
            ],
            'conflictStatus': 'NONE',
        },
        {
            'analysisStatus': 'READY',
            'analysisIssues': [],
            'conflictStatus': 'UNKNOWN',
        },
        {
            'analysisStatus': 'READY',
            'analysisIssues': [],
            'conflictStatus': {'status': 'NONE'},
        },
        {
            'analysisStatus': 'PARTIAL',
            'analysisIssues': [],
            'conflictStatus': 'NONE',
        },
        {
            'analysisStatus': 'READY',
            'analysisIssues': [
                {
                    'code': 'CONFLICT_CHECK_FAILED',
                    'message': 'provider secret',
                }
            ],
            'conflictStatus': 'NONE',
        },
    ],
    ids=[
        'metadata-missing',
        'issues-wrong-type',
        'unknown-status',
        'wrong-status-type',
        'unknown-issue-code',
        'unknown-conflict-status',
        'wrong-conflict-status-type',
        'partial-without-issue',
        'ready-with-degradation-issue',
    ],
)
def test_cluster_builder_fails_closed_for_invalid_success_metadata(
    metadata,
    sample_cluster_row,
    sample_processed_article_rows,
):
    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        _summary_record(
            paragraphs=_grounded_sections(),
            metadata=metadata,
        ),
    )

    assert payload['summary'] == {
        'short': sample_cluster_row['summary_short'],
        'long': sample_cluster_row['summary_long'],
        'analysisStatus': 'UNAVAILABLE',
        'analysisGeneratedAt': None,
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
        'sections': [],
    }


def test_cluster_builder_structural_failure_wins_over_persisted_metadata(
    sample_cluster_row,
    sample_processed_article_rows,
):
    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        _summary_record(
            paragraphs=['legacy paragraph'],
            metadata={
                'analysisStatus': 'PARTIAL',
                'analysisIssues': [
                    {
                        'code': 'INVALID_SOURCE_REFERENCE',
                        'message': 'provider secret',
                    }
                ],
                'conflictStatus': 'NOT_CHECKED',
            },
        ),
    )

    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }
    ]
    assert payload['summary']['conflictStatus'] == 'NOT_CHECKED'
    assert payload['summary']['sections'] == []


def test_cluster_builder_rejects_metadata_conflict_aggregate_mismatch(
    sample_cluster_row,
    sample_processed_article_rows,
):
    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        _summary_record(
            paragraphs=_grounded_sections(),
            metadata={
                'analysisStatus': 'READY',
                'analysisIssues': [],
                'conflictStatus': 'FOUND',
            },
        ),
    )

    assert payload['summary']['analysisStatus'] == 'UNAVAILABLE'
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }
    ]


def test_cluster_builder_merges_valid_causal_metadata_and_validator_issues(
    sample_cluster_row,
    sample_processed_article_rows,
):
    payload = build_cluster_detail_payload(
        sample_cluster_row,
        sample_processed_article_rows[0],
        sample_processed_article_rows,
        _summary_record(
            paragraphs=_grounded_sections(conflict_status='NOT_CHECKED'),
            metadata={
                'analysisStatus': 'PARTIAL',
                'analysisIssues': [
                    {
                        'code': 'INVALID_SOURCE_REFERENCE',
                        'message': 'provider secret',
                    },
                    {
                        'code': 'CONFLICT_CHECK_FAILED',
                        'message': 'another provider secret',
                    },
                ],
                'conflictStatus': 'NOT_CHECKED',
            },
        ),
    )

    assert payload['summary']['analysisStatus'] == 'PARTIAL'
    assert payload['summary']['analysisIssues'] == [
        {
            'code': 'INVALID_SOURCE_REFERENCE',
            'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
        },
        {
            'code': 'CONFLICT_CHECK_FAILED',
            'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
        },
    ]
    assert payload['summary']['conflictStatus'] == 'NOT_CHECKED'


@pytest.mark.parametrize(
    'field',
    ['similarGroupId', 'isSimilarGroupRepresentative', 'exactDuplicateCount'],
)
def test_cluster_article_grouping_fields_are_required(
    field,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    del payload['articles'][0][field]

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_cluster_article_grouping_status_is_required(
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    del payload['articleGrouping']

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


@pytest.mark.parametrize(
    'article_grouping',
    [
        {'status': 'UNAVAILABLE', 'generatedAt': ANALYSIS_GENERATED_AT, 'issue': None},
        {'status': 'UNAVAILABLE', 'generatedAt': None, 'issue': None},
    ],
    ids=['generated-at-present', 'issue-missing'],
)
def test_unavailable_article_grouping_requires_truthful_state(
    article_grouping,
    sample_cluster_detail_payload,
):
    payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload['articleGrouping'] = article_grouping

    with pytest.raises(ValidationError):
        assemble_cluster_detail_response(payload)


def test_cluster_assembler_returns_full_detail_contract(sample_cluster_detail_payload):
    structured_payload = _structured_cluster_payload(sample_cluster_detail_payload)
    payload = {
        **structured_payload,
        'articleCount': 3,
        'representativeArticle': {
            **structured_payload['representativeArticle'],
            'sourceSummary': '대표 기사 요약',
        },
        'articles': [
            {**article, 'sourceSummary': f'기사 요약 {index}'}
            for index, article in enumerate(structured_payload['articles'], start=1)
        ],
    }
    response = jsonable(assemble_cluster_detail_response(payload))

    assert response['clusterId'] == sample_cluster_detail_payload['clusterId']
    assert response['marketType'] == 'US'
    assert response['summary']['short'] == '반도체 업종 강세가 나스닥 상승을 견인했다.'
    assert response['representativeArticle']['publisherName'] == '매일경제'
    assert response['representativeArticle']['sourceSummary'] == '대표 기사 요약'
    assert response['articles'][0]['title'] == '엔비디아 급등에 반도체 강세'
    assert response['articles'][0]['sourceSummary'] == '기사 요약 1'
    assert response['articles'][-1]['title'] == '대형 기술주 재평가로 나스닥 반등'
    assert response['lastUpdatedAt'] == '2026-03-18T06:12:10Z'
    assert response['articleCount'] == 3
