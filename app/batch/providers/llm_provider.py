from __future__ import annotations

import json
import math
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.core.llm import GeminiJsonClient

PROMPT_VERSION = 'v2'


def _json_safe(value: Any, *, active_container_ids: set[int] | None = None) -> Any:
    active_ids = active_container_ids if active_container_ids is not None else set()
    if isinstance(value, dict):
        container_id = id(value)
        if container_id in active_ids:
            raise ValueError('LLM prompt payload contains a container cycle.')
        active_ids.add(container_id)
        try:
            normalized: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise TypeError('LLM prompt payload object keys must be strings.')
                normalized[key] = _json_safe(
                    item,
                    active_container_ids=active_ids,
                )
            return normalized
        finally:
            active_ids.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_ids:
            raise ValueError('LLM prompt payload contains a container cycle.')
        active_ids.add(container_id)
        try:
            return [_json_safe(item, active_container_ids=active_ids) for item in value]
        finally:
            active_ids.remove(container_id)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError('LLM prompt payload contains a non-finite number.')
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('LLM prompt payload contains a non-finite number.')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError(
        f'Unsupported LLM prompt payload value type: {type(value).__name__}.'
    )


def _serialize_prompt(payload: dict[str, Any]) -> str:
    return json.dumps(_json_safe(payload), ensure_ascii=False, allow_nan=False)


class BatchLlmProvider:
    def __init__(self, client: GeminiJsonClient | None = None) -> None:
        self._client = client or GeminiJsonClient()

    def is_configured(self) -> bool:
        return self._client.is_configured()

    @property
    def model_name(self) -> str:
        return self._client.model_name

    @property
    def concurrency_limit(self) -> int:
        return self._client.concurrency_limit

    async def enrich_cluster(
        self,
        *,
        market_type: str,
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial news clustering assistant. '
            'Return a single JSON object with keys: title, summary_short, '
            'summary_long, tags, representative_article_index, '
            'analysis_paragraphs.'
        )
        user_prompt = _serialize_prompt(
            {
                'marketType': market_type,
                'articles': articles,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )

    async def summarize_market(
        self,
        *,
        market_type: str,
        indices: list[dict[str, Any]],
        clusters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial market summarizer. '
            'Return a JSON object with string fields title, body, and outlook. '
            'The background and key_themes fields must each be JSON arrays '
            'containing only strings.'
        )
        user_prompt = _serialize_prompt(
            {
                'marketType': market_type,
                'indices': indices,
                'clusters': clusters,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )

    async def summarize_global_headline(
        self,
        *,
        clusters: list[dict[str, Any]],
        indices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial news editor. Return a JSON object with keys: '
            'title, body.'
        )
        user_prompt = _serialize_prompt(
            {
                'clusters': clusters,
                'indices': indices,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )

    async def summarize_key_points(
        self,
        *,
        clusters: list[dict[str, Any]],
        indices: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial news editor. Return one JSON object whose '
            'keyPoints field is an array containing exactly three objects in '
            'this exact order and with no additional fields: '
            '1. {"kind": "direction", "label": "시장 방향", "text": '
            '"one complete plain-text sentence", "direction": one of '
            '"UP", "DOWN", "MIXED", or "FLAT"}; '
            '2. {"kind": "driver", "label": "주요 원인", "text": '
            '"one complete plain-text sentence"}; '
            '3. {"kind": "watch", "label": "관전 포인트", "text": '
            '"one complete plain-text sentence"}. '
            'Do not use HTML, Markdown, or line breaks in text.'
        )
        user_prompt = _serialize_prompt(
            {
                'clusters': clusters,
                'indices': indices,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )

    async def summarize_cluster_card(
        self,
        *,
        market_type: str,
        cluster: dict[str, Any],
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial news card summarizer. Return a JSON object with '
            'keys: title, body.'
        )
        user_prompt = _serialize_prompt(
            {
                'marketType': market_type,
                'cluster': cluster,
                'articles': articles,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )

    async def summarize_cluster_detail(
        self,
        *,
        market_type: str,
        cluster: dict[str, Any],
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        system_prompt = (
            'You are a financial news analyst. Return a JSON object with string '
            'fields title and body. The paragraphs field must be a JSON array '
            'containing only strings.'
        )
        user_prompt = _serialize_prompt(
            {
                'marketType': market_type,
                'cluster': cluster,
                'articles': articles,
            }
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )


__all__ = ['BatchLlmProvider', 'PROMPT_VERSION']
