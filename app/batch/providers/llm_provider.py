from __future__ import annotations

import json
from typing import Any

from app.core.llm import GeminiJsonClient

PROMPT_VERSION = 'v1'


class BatchLlmProvider:
    def __init__(self, client: GeminiJsonClient | None = None) -> None:
        self._client = client or GeminiJsonClient()

    def is_configured(self) -> bool:
        return self._client.is_configured()

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
        user_prompt = json.dumps(
            {
                'marketType': market_type,
                'articles': articles,
            },
            ensure_ascii=False,
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
            'Return a JSON object with keys: title, body, background, '
            'key_themes, outlook.'
        )
        user_prompt = json.dumps(
            {
                'marketType': market_type,
                'indices': indices,
                'clusters': clusters,
            },
            ensure_ascii=False,
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
        user_prompt = json.dumps(
            {
                'clusters': clusters,
                'indices': indices,
            },
            ensure_ascii=False,
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
        user_prompt = json.dumps(
            {
                'marketType': market_type,
                'cluster': cluster,
                'articles': articles,
            },
            ensure_ascii=False,
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
            'You are a financial news analyst. Return a JSON object with keys: '
            'title, body, paragraphs.'
        )
        user_prompt = json.dumps(
            {
                'marketType': market_type,
                'cluster': cluster,
                'articles': articles,
            },
            ensure_ascii=False,
        )
        return await self._client.invoke_json(
            system_prompt=system_prompt, user_prompt=user_prompt
        )


__all__ = ['BatchLlmProvider', 'PROMPT_VERSION']
