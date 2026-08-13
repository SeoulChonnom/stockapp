from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.batch.providers.llm_provider import BatchLlmProvider
from tests.batch.gemini_mock import MockGeminiApiClient, gemini_json_response


class RecordingClient:
    def __init__(self) -> None:
        self.user_prompt: str | None = None

    def is_configured(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return 'test-model'

    @property
    def concurrency_limit(self) -> int:
        return 1

    async def invoke_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        _ = system_prompt
        self.user_prompt = user_prompt
        return {}


@pytest.mark.anyio
async def test_cluster_prompt_serializes_nested_domain_values_without_mutation():
    client = RecordingClient()
    provider = BatchLlmProvider(client)
    published_at = datetime(2026, 7, 28, 15, 0, tzinfo=UTC)
    business_date = date(2026, 7, 29)
    cluster_id = UUID('00000000-0000-0000-0000-000000000123')
    article = {
        'business_date': business_date,
        'published_at': published_at,
        'score': Decimal('1.25'),
        'cluster_id': cluster_id,
        'nested': [{'source_date': business_date}],
    }

    await provider.summarize_cluster_card(
        market_type='US',
        cluster={'title': 'Title'},
        articles=[article],
    )

    assert client.user_prompt is not None
    payload = json.loads(client.user_prompt)
    serialized = payload['articles'][0]
    assert serialized['business_date'] == '2026-07-29'
    assert serialized['published_at'] == '2026-07-28T15:00:00+00:00'
    assert serialized['score'] == '1.25'
    assert serialized['cluster_id'] == str(cluster_id)
    assert serialized['nested'][0]['source_date'] == '2026-07-29'
    assert article['business_date'] is business_date
    assert article['published_at'] is published_at
    assert article['score'] == Decimal('1.25')
    assert article['cluster_id'] is cluster_id


@pytest.mark.anyio
async def test_cluster_prompt_rejects_non_string_object_keys():
    provider = BatchLlmProvider(RecordingClient())

    with pytest.raises(
        TypeError,
        match='LLM prompt payload object keys must be strings',
    ):
        await provider.summarize_cluster_card(
            market_type='US',
            cluster={'title': 'Title'},
            articles=[{1: 'not allowed'}],
        )


@pytest.mark.anyio
async def test_cluster_prompt_rejects_container_cycles():
    provider = BatchLlmProvider(RecordingClient())
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(
        ValueError, match='LLM prompt payload contains a container cycle'
    ):
        await provider.summarize_cluster_card(
            market_type='US',
            cluster={'title': 'Title'},
            articles=[{'cyclic': cyclic}],
        )


@pytest.mark.anyio
async def test_cluster_prompt_rejects_unsupported_value_types():
    provider = BatchLlmProvider(RecordingClient())

    with pytest.raises(
        TypeError,
        match='Unsupported LLM prompt payload value type: set',
    ):
        await provider.summarize_cluster_card(
            market_type='US',
            cluster={'title': 'Title'},
            articles=[{'unsupported': {'value'}}],
        )


@pytest.mark.anyio
@pytest.mark.parametrize('value', [float('nan'), float('inf'), Decimal('Infinity')])
async def test_cluster_prompt_rejects_non_finite_numbers(value):
    provider = BatchLlmProvider(RecordingClient())

    with pytest.raises(
        ValueError,
        match='LLM prompt payload contains a non-finite number',
    ):
        await provider.summarize_cluster_card(
            market_type='US',
            cluster={'title': 'Title'},
            articles=[{'value': value}],
        )


@pytest.mark.anyio
async def test_key_point_prompt_serializes_only_cluster_and_index_evidence():
    key_points = [
        {
            'kind': 'direction',
            'label': '시장 방향',
            'text': '기술주 중심으로 상승했습니다.',
            'direction': 'UP',
        },
        {
            'kind': 'driver',
            'label': '주요 원인',
            'text': '반도체 실적 기대가 지수를 끌어올렸습니다.',
        },
        {
            'kind': 'watch',
            'label': '관전 포인트',
            'text': '다음 물가 지표를 확인해야 합니다.',
        },
    ]
    client = MockGeminiApiClient(
        [(200, gemini_json_response({'keyPoints': key_points}))]
    )
    provider = BatchLlmProvider(client)
    clusters = [{'title': '반도체 강세', 'summary': 'AI 수요 기대가 높아졌습니다.'}]
    indices = [{'marketType': 'US', 'indexCode': '^IXIC', 'changePercent': '1.25'}]

    result = await provider.summarize_key_points(
        clusters=clusters,
        indices=indices,
    )

    assert result == {'keyPoints': key_points}
    serialized_request = client.request_payloads[0]
    system_prompt = serialized_request['systemInstruction']['parts'][0]['text']
    assert 'exactly three objects' in system_prompt
    assert '"kind": "direction"' in system_prompt
    assert '"label": "시장 방향"' in system_prompt
    assert '"kind": "driver"' in system_prompt
    assert '"label": "주요 원인"' in system_prompt
    assert '"kind": "watch"' in system_prompt
    assert '"label": "관전 포인트"' in system_prompt
    assert '"UP", "DOWN", "MIXED", or "FLAT"' in system_prompt
    user_prompt = serialized_request['contents'][0]['parts'][0]['text']
    assert json.loads(user_prompt) == {
        'clusters': clusters,
        'indices': indices,
    }
