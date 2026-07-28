from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from app.batch.providers.llm_provider import BatchLlmProvider


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
