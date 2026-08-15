from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.generate_ai_summaries import (
    _generate_cluster_card_summary,
    _generate_cluster_detail_summary,
    _generate_global_outputs,
    _generate_market_summary,
)
from app.core.llm import (
    LlmRetryableError,
    LlmRetryExhaustedError,
    llm_retry_exhausted_mode,
)
from tests.batch.gemini_mock import (
    build_mock_gemini_harness,
    gemini_ai_message,
    malformed_gemini_ai_message,
)

HEADLINE = {'title': '기술주가 이끈 글로벌 증시 반등', 'body': '반도체가 강세였습니다.'}
KEY_POINTS = [
    {
        'kind': 'direction',
        'label': '시장 방향',
        'text': '글로벌 증시는 상승했습니다.',
        'direction': 'UP',
    },
    {
        'kind': 'driver',
        'label': '주요 원인',
        'text': '반도체 실적 기대가 상승을 이끌었습니다.',
    },
    {
        'kind': 'watch',
        'label': '관전 포인트',
        'text': '물가 지표와 금리 경로를 지켜봐야 합니다.',
    },
]
# Settings.llm_call_max_attempts default: attempts made inside one invoke_json.
CALL_ATTEMPTS = 3
CLUSTERS = [
    {
        'title': '반도체 강세',
        'summary_short': 'AI 수요 기대가 지수를 끌어올렸습니다.',
    }
]
CLUSTER_DETAIL_CLUSTER = {
    'title': '반도체주 조정',
    'summary_short': '반도체주가 하락했습니다.',
    'summary_long': '외국인 매도와 업황 우려가 함께 반영됐습니다.',
    'analysis_paragraphs_json': ['이 레거시 문단은 사용하지 않습니다.'],
}
CLUSTER_DETAIL_ARTICLES = [
    {
        'id': 1024,
        'canonical_title': '반도체주 약세',
        'source_summary': '외국인 매도가 이어졌습니다.',
        'article_body_excerpt': '반도체 업종이 하락했습니다.',
        'origin_link': 'https://example.com/1024',
    },
    {
        'id': 1042,
        'canonical_title': '기관은 반도체주 매수',
        'source_summary': '기관은 일부 대형주를 순매수했습니다.',
        'article_body_excerpt': '수급 주체별 방향이 엇갈렸습니다.',
        'origin_link': 'https://example.com/1042',
    },
]


class StringListLlmProvider:
    model_name = 'test-model'

    def is_configured(self) -> bool:
        return True

    async def summarize_market(self, **_kwargs) -> dict:
        return {
            'title': '시장 요약',
            'body': '시장 본문',
            'background': '단일 배경',
            'key_themes': '단일 테마',
            'outlook': '시장 전망',
        }


@pytest.mark.anyio
async def test_market_summary_normalizes_string_list_fields():
    result = await _generate_market_summary(
        StringListLlmProvider(),
        market_type='US',
        clusters=[
            {
                'title': '클러스터',
                'summary_short': '짧은 요약',
                'summary_long': '긴 요약',
                'tags_json': ['테마'],
            }
        ],
        indices=[],
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json']['background'] == ['단일 배경']
    assert result['metadata_json']['keyThemes'] == ['단일 테마']


@pytest.mark.anyio
async def test_cluster_card_prompt_excludes_raw_article_content(monkeypatch):
    """A processed row's content_json must never reach the card summary prompt.

    ``get_processed_articles`` returns ``content_json``, which carries the full
    article body and the raw provider payload. Serializing those inflates the
    prompt past the token budget and leaks the provider payload, so the prompt
    is built from the capped title/summary/excerpt projection instead.
    """
    body_text = '본문' * 20_000
    articles = [
        {
            **article,
            'content_json': {
                'bodyText': body_text,
                'payload': {'description': body_text, 'link': 'https://example.com'},
                'providerName': 'NAVER_NEWS',
            },
        }
        for article in CLUSTER_DETAIL_ARTICLES
    ]
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(
                {'title': '반도체주 조정', 'body': '반도체주가 하락했습니다.'}
            )
        ],
    )

    result = await _generate_cluster_card_summary(
        BatchLlmProvider(harness.client),
        'KR',
        CLUSTER_DETAIL_CLUSTER,
        articles,
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False

    user_prompt = harness.model.messages[0][1][1]
    assert body_text not in user_prompt
    assert 'content_json' not in user_prompt
    assert 'bodyText' not in user_prompt
    assert 'providerName' not in user_prompt
    assert '반도체주 약세' in user_prompt
    assert harness.token_limiter.estimates[0] < 2_000


def _analysis_sentence(**overrides: object) -> dict[str, object]:
    sentence: dict[str, object] = {
        'text': '미국 반도체주 약세가 국내 시장으로 이어졌습니다.',
        'sourceArticleIds': [1024],
        'conflictStatus': 'NONE',
        'conflictingSourceArticleIds': [],
        'conflictNote': None,
    }
    sentence.update(overrides)
    return sentence


def _analysis_payload(*sentences: dict[str, object]) -> dict[str, object]:
    return {
        'sections': [
            {
                'kind': 'impact',
                'title': '시장 영향',
                'paragraphs': [{'sentences': list(sentences)}],
            }
        ]
    }


async def _generate_cluster_detail_with_gemini(
    monkeypatch,
    response: object,
) -> tuple[dict, object]:
    harness = build_mock_gemini_harness(monkeypatch, [response])
    result = await _generate_cluster_detail_summary(
        BatchLlmProvider(harness.client),
        'KR',
        CLUSTER_DETAIL_CLUSTER,
        CLUSTER_DETAIL_ARTICLES,
    )
    return result, harness


@pytest.mark.anyio
async def test_cluster_detail_persists_fully_grounded_sections_ready(monkeypatch):
    provider_payload = _analysis_payload(
        _analysis_sentence(),
        _analysis_sentence(
            text='기사별 외국인 수급 방향은 다르게 보도됐습니다.',
            sourceArticleIds=[1024],
            conflictStatus='FOUND',
            conflictingSourceArticleIds=[1042],
            conflictNote='기사별 외국인 순매매 방향이 다르게 보도됐습니다.',
        ),
    )

    result, harness = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(provider_payload),
    )

    assert result == {
        'title': '반도체주 조정',
        'body': '외국인 매도와 업황 우려가 함께 반영됐습니다.',
        'paragraphs': provider_payload['sections'],
        'status': 'SUCCESS',
        'fallback_used': False,
        'model_name': 'gemini-2.5-flash',
        'metadata_json': {
            'analysisStatus': 'READY',
            'analysisIssues': [],
            'conflictStatus': 'FOUND',
        },
    }
    user_payload = json.loads(harness.model.messages[0][1][1])
    assert user_payload['articles'] == [
        {
            'processedArticleId': 1024,
            'title': '반도체주 약세',
            'summary': '외국인 매도가 이어졌습니다.',
            'excerpt': '반도체 업종이 하락했습니다.',
        },
        {
            'processedArticleId': 1042,
            'title': '기관은 반도체주 매수',
            'summary': '기관은 일부 대형주를 순매수했습니다.',
            'excerpt': '수급 주체별 방향이 엇갈렸습니다.',
        },
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    'invalid_source_ids',
    [
        pytest.param([9999], id='unknown'),
        pytest.param([], id='empty'),
        pytest.param([1024, 1024], id='duplicate'),
    ],
)
async def test_cluster_detail_prunes_invalid_primary_sentence_only(
    monkeypatch,
    invalid_source_ids,
):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(
            _analysis_payload(
                _analysis_sentence(sourceArticleIds=invalid_source_ids),
                _analysis_sentence(text='유효한 근거 문장은 남습니다.'),
            )
        ),
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['paragraphs'] == [
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
    ]
    assert result['metadata_json'] == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'INVALID_SOURCE_REFERENCE',
                'message': '일부 분석 문장의 근거 기사를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NONE',
    }


@pytest.mark.anyio
async def test_cluster_detail_all_invalid_primary_sentences_are_unavailable(
    monkeypatch,
):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(
            _analysis_payload(
                _analysis_sentence(sourceArticleIds=[9999]),
                _analysis_sentence(sourceArticleIds=[]),
                _analysis_sentence(sourceArticleIds=[1024, 1024]),
            )
        ),
    )

    assert result['paragraphs'] == []
    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json'] == {
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
    }
    assert repr(result).count('INVALID_SOURCE_REFERENCE') == 1
    assert repr(result).count('NO_GROUNDED_SENTENCES') == 1
    assert '이 레거시 문단은 사용하지 않습니다.' not in repr(result)


@pytest.mark.anyio
async def test_cluster_detail_degrades_only_malformed_conflict_evidence(monkeypatch):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(
            _analysis_payload(
                _analysis_sentence(
                    text='근거가 있는 분석 문장은 유지됩니다.',
                    conflictStatus='FOUND',
                    conflictingSourceArticleIds=[1024],
                    conflictNote='primary 근거와 중복됩니다.',
                )
            )
        ),
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json'] == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
    }
    assert result['paragraphs'][0]['paragraphs'][0]['sentences'] == [
        {
            'text': '근거가 있는 분석 문장은 유지됩니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'NOT_CHECKED',
            'conflictingSourceArticleIds': [],
            'conflictNote': None,
        }
    ]


@pytest.mark.anyio
async def test_cluster_detail_retains_not_checked_sentence_as_partial(monkeypatch):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(
            _analysis_payload(
                _analysis_sentence(
                    text='충돌 여부는 확인하지 못했지만 근거 문장은 유지됩니다.',
                    conflictStatus='NOT_CHECKED',
                )
            )
        ),
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json'] == {
        'analysisStatus': 'PARTIAL',
        'analysisIssues': [
            {
                'code': 'CONFLICT_CHECK_FAILED',
                'message': '일부 분석 문장의 충돌 근거를 확인하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
    }
    assert result['paragraphs'][0]['paragraphs'][0]['sentences'] == [
        {
            'text': '충돌 여부는 확인하지 못했지만 근거 문장은 유지됩니다.',
            'sourceArticleIds': [1024],
            'conflictStatus': 'NOT_CHECKED',
            'conflictingSourceArticleIds': [],
            'conflictNote': None,
        }
    ]


@pytest.mark.anyio
async def test_cluster_detail_empty_sections_are_unavailable(monkeypatch):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message({'sections': []}),
    )

    assert result['paragraphs'] == []
    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json'] == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'NO_GROUNDED_SENTENCES',
                'message': '근거를 확인할 수 있는 분석 문장이 없습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    'malformed_payload',
    [
        pytest.param({'sections': {}}, id='non-array-sections'),
        pytest.param(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': [
                            {'sentences': [_analysis_sentence(text='유효한 형제')]}
                        ],
                    },
                    None,
                ]
            },
            id='non-object-section-with-valid-sibling',
        ),
        pytest.param(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': {},
                    }
                ]
            },
            id='non-array-paragraphs',
        ),
        pytest.param(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': [
                            {'sentences': [_analysis_sentence(text='유효한 형제')]},
                            None,
                        ],
                    }
                ]
            },
            id='non-object-paragraph-with-valid-sibling',
        ),
        pytest.param(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': [{'sentences': {}}],
                    }
                ]
            },
            id='non-array-sentences',
        ),
        pytest.param(
            {
                'sections': [
                    {
                        'kind': 'impact',
                        'title': '시장 영향',
                        'paragraphs': [
                            {
                                'sentences': [
                                    _analysis_sentence(text='유효한 형제'),
                                    None,
                                ]
                            }
                        ],
                    }
                ]
            },
            id='non-object-sentence-with-valid-sibling',
        ),
    ],
)
async def test_cluster_detail_malformed_structure_discards_valid_siblings(
    monkeypatch,
    malformed_payload,
):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        gemini_ai_message(malformed_payload),
    )

    assert result['paragraphs'] == []
    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json'] == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
    }
    assert '유효한 형제' not in repr(result)


@pytest.mark.anyio
async def test_cluster_detail_malformed_provider_json_is_unavailable(monkeypatch):
    result, _ = await _generate_cluster_detail_with_gemini(
        monkeypatch,
        malformed_gemini_ai_message('{"sections":'),
    )

    assert result['paragraphs'] == []
    assert result['status'] == 'FALLBACK'
    assert result['metadata_json']['analysisIssues'] == [
        {
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }
    ]


@pytest.mark.anyio
async def test_cluster_detail_provider_exhaustion_is_unavailable(monkeypatch):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [httpx.ConnectError('secret-project-token provider disconnected')],
    )

    with llm_retry_exhausted_mode():
        result = await _generate_cluster_detail_summary(
            BatchLlmProvider(harness.client),
            'KR',
            CLUSTER_DETAIL_CLUSTER,
            CLUSTER_DETAIL_ARTICLES,
        )

    assert result['paragraphs'] == []
    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json'] == {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [
            {
                'code': 'ANALYSIS_GENERATION_FAILED',
                'message': '분석을 생성하지 못했습니다.',
            }
        ],
        'conflictStatus': 'NOT_CHECKED',
    }
    assert 'secret-project-token' not in repr(result)


@pytest.mark.anyio
async def test_cluster_detail_propagates_transient_error(monkeypatch):
    # Every in-call attempt must fail before the error is deferred to the
    # durable worker; a provider that recovers on retry never gets this far.
    harness = build_mock_gemini_harness(
        monkeypatch,
        [httpx.ReadTimeout('cluster detail provider timed out')] * CALL_ATTEMPTS,
    )

    with pytest.raises(LlmRetryableError):
        await _generate_cluster_detail_summary(
            BatchLlmProvider(harness.client),
            'KR',
            CLUSTER_DETAIL_CLUSTER,
            CLUSTER_DETAIL_ARTICLES,
        )

    assert harness.model.call_count == CALL_ATTEMPTS


@pytest.mark.anyio
async def test_cluster_detail_propagates_cancellation(monkeypatch):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [asyncio.CancelledError()],
    )

    with pytest.raises(asyncio.CancelledError):
        await _generate_cluster_detail_summary(
            BatchLlmProvider(harness.client),
            'KR',
            CLUSTER_DETAIL_CLUSTER,
            CLUSTER_DETAIL_ARTICLES,
        )


@pytest.mark.anyio
async def test_market_summary_keeps_structured_non_string_list_as_fallback():
    class InvalidLlmProvider(StringListLlmProvider):
        async def summarize_market(self, **_kwargs) -> dict:
            return {
                'title': '시장 요약',
                'body': '시장 본문',
                'background': {'unexpected': 'object'},
                'key_themes': ['테마'],
                'outlook': '시장 전망',
            }

    result = await _generate_market_summary(
        InvalidLlmProvider(),
        market_type='US',
        clusters=[
            {
                'title': '클러스터',
                'summary_short': '짧은 요약',
                'summary_long': '긴 요약',
                'tags_json': ['테마'],
            }
        ],
        indices=[],
    )

    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json']['reason'] == 'llm_malformed_response'


@pytest.mark.anyio
async def test_market_summary_propagates_retryable_error_to_durable_worker():
    class RetryableProvider(StringListLlmProvider):
        async def summarize_market(self, **_kwargs) -> dict:
            raise LlmRetryableError(retry_after_seconds=30)

    with pytest.raises(LlmRetryableError):
        await _generate_market_summary(
            RetryableProvider(),
            market_type='US',
            clusters=[],
            indices=[],
        )


@pytest.mark.anyio
async def test_market_summary_uses_fallback_after_durable_retries_exhausted():
    class ExhaustedProvider(StringListLlmProvider):
        async def summarize_market(self, **_kwargs) -> dict:
            raise LlmRetryExhaustedError('sanitized terminal error')

    result = await _generate_market_summary(
        ExhaustedProvider(),
        market_type='US',
        clusters=[],
        indices=[],
    )

    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['error_message'] == (
        'AI provider request failed; fallback content was used.'
    )
    assert 'sanitized terminal error' not in repr(result)


@pytest.mark.anyio
async def test_global_outputs_preserve_headline_when_key_points_are_malformed(
    monkeypatch,
):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            gemini_ai_message({'keyPoints': [{'kind': 'direction'}]}),
        ],
    )

    result = await _generate_global_outputs(
        BatchLlmProvider(harness.client),
        CLUSTERS,
        [],
    )

    assert result['title'] == HEADLINE['title']
    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json']['keyPoints'] == []
    assert result['metadata_json']['keyPointIssue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }


@pytest.mark.anyio
async def test_global_outputs_treat_malformed_key_point_json_as_fixed_issue(
    monkeypatch,
):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            malformed_gemini_ai_message(),
        ],
    )

    result = await _generate_global_outputs(
        BatchLlmProvider(harness.client),
        CLUSTERS,
        [],
    )

    assert result['title'] == HEADLINE['title']
    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json']['keyPoints'] == []
    assert result['metadata_json']['keyPointIssue'] == {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '오늘의 핵심 포인트를 준비하지 못했습니다.',
    }


@pytest.mark.anyio
async def test_global_outputs_preserve_headline_when_key_point_provider_exhausts(
    monkeypatch,
):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            httpx.ConnectError('secret-project-token provider disconnected'),
        ],
    )

    with llm_retry_exhausted_mode():
        result = await _generate_global_outputs(
            BatchLlmProvider(harness.client),
            CLUSTERS,
            [],
        )

    assert result['title'] == HEADLINE['title']
    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['metadata_json']['keyPoints'] == []
    assert result['metadata_json']['keyPointIssue']['code'] == (
        'KEY_POINTS_GENERATION_FAILED'
    )
    assert 'secret-project-token' not in repr(result)


@pytest.mark.anyio
async def test_global_outputs_preserve_key_points_when_headline_provider_exhausts(
    monkeypatch,
):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            httpx.ConnectError('headline provider disconnected'),
            gemini_ai_message({'keyPoints': KEY_POINTS}),
        ],
    )

    with llm_retry_exhausted_mode():
        result = await _generate_global_outputs(
            BatchLlmProvider(harness.client),
            CLUSTERS,
            [],
        )

    assert result['status'] == 'FALLBACK'
    assert result['fallback_used'] is True
    assert result['metadata_json']['keyPoints'] == KEY_POINTS
    assert result['metadata_json']['keyPointIssue'] is None


@pytest.mark.anyio
async def test_global_outputs_store_headline_and_key_points_when_both_succeed(
    monkeypatch,
):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            gemini_ai_message({'keyPoints': KEY_POINTS}),
        ],
    )

    result = await _generate_global_outputs(
        BatchLlmProvider(harness.client),
        CLUSTERS,
        [],
    )

    assert result == {
        'title': HEADLINE['title'],
        'body': HEADLINE['body'],
        'status': 'SUCCESS',
        'fallback_used': False,
        'model_name': 'gemini-2.5-flash',
        'metadata_json': {
            'reason': 'llm',
            'keyPoints': KEY_POINTS,
            'keyPointIssue': None,
        },
    }


@pytest.mark.anyio
async def test_global_outputs_propagate_key_point_transient_error(monkeypatch):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            *([httpx.ReadTimeout('key point provider timed out')] * CALL_ATTEMPTS),
        ],
    )

    with pytest.raises(LlmRetryableError):
        await _generate_global_outputs(
            BatchLlmProvider(harness.client),
            CLUSTERS,
            [],
        )


@pytest.mark.anyio
async def test_global_outputs_propagate_key_point_cancellation(monkeypatch):
    harness = build_mock_gemini_harness(
        monkeypatch,
        [
            gemini_ai_message(HEADLINE),
            asyncio.CancelledError(),
        ],
    )

    with pytest.raises(asyncio.CancelledError):
        await _generate_global_outputs(
            BatchLlmProvider(harness.client),
            CLUSTERS,
            [],
        )
