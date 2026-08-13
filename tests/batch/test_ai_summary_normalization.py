from __future__ import annotations

import asyncio

import httpx
import pytest

from app.batch.providers.llm_provider import BatchLlmProvider
from app.batch.steps.generate_ai_summaries import (
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
CLUSTERS = [
    {
        'title': '반도체 강세',
        'summary_short': 'AI 수요 기대가 지수를 끌어올렸습니다.',
    }
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

    async def summarize_cluster_detail(self, **_kwargs) -> dict:
        return {
            'title': '클러스터 상세',
            'body': ['상세 본문 1', '상세 본문 2'],
            'paragraphs': '단일 문단',
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
async def test_cluster_detail_normalizes_string_paragraphs():
    result = await _generate_cluster_detail_summary(
        StringListLlmProvider(),
        'KR',
        {
            'title': '클러스터',
            'summary_short': '짧은 요약',
            'summary_long': '긴 요약',
            'analysis_paragraphs_json': ['기존 문단'],
        },
        [],
    )

    assert result['status'] == 'SUCCESS'
    assert result['fallback_used'] is False
    assert result['body'] == '상세 본문 1\n\n상세 본문 2'
    assert result['paragraphs'] == ['단일 문단']


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
            httpx.ReadTimeout('key point provider timed out'),
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
