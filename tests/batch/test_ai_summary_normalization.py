from __future__ import annotations

import pytest

from app.batch.steps.generate_ai_summaries import (
    _generate_cluster_detail_summary,
    _generate_market_summary,
)


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
