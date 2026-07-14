from __future__ import annotations

import asyncio
import json
from typing import Any

from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.settings import Settings, get_settings


class LlmConfigurationError(RuntimeError):
    pass


class LlmTimeoutError(RuntimeError):
    pass


class GeminiJsonClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    @property
    def concurrency_limit(self) -> int:
        return self._settings.llm_concurrency_limit

    def _build_model(self) -> ChatGoogleGenerativeAI:
        if not self.is_configured():
            raise LlmConfigurationError('Gemini API key is not configured.')
        return ChatGoogleGenerativeAI(
            model=self._settings.llm_model,
            google_api_key=self._settings.gemini_api_key,
            temperature=self._settings.llm_temperature,
            max_retries=self._settings.llm_max_retries,
        )

    async def invoke_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        model = self._build_model()
        try:
            response = await asyncio.wait_for(
                model.ainvoke(
                    [
                        ('system', system_prompt),
                        ('human', user_prompt),
                    ]
                ),
                timeout=self._settings.llm_timeout_seconds,
            )
        except TimeoutError as exc:
            raise LlmTimeoutError('LLM invocation timed out.') from exc
        return self._parse_json(str(response.content))

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        normalized = content.strip()
        if normalized.startswith('```'):
            normalized = normalized.strip('`')
            if normalized.startswith('json'):
                normalized = normalized[4:].strip()
        parsed = json.loads(normalized)
        if not isinstance(parsed, dict):
            raise ValueError('Expected a JSON object from the LLM response.')
        return parsed


__all__ = ['GeminiJsonClient', 'LlmConfigurationError', 'LlmTimeoutError']
