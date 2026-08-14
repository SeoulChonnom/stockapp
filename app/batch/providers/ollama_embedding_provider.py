from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any, Protocol

import httpx

from app.core.settings import Settings, get_settings
from app.core.text import normalize_text


class OllamaEmbeddingError(RuntimeError):
    """Sanitized error raised when Ollama cannot provide valid embeddings."""


@dataclass(frozen=True, slots=True)
class EmbeddingArticle:
    """Article fields used to build one deterministic embedding input."""

    canonical_title: str | None = None
    source_summary: str | None = None
    article_body_excerpt: str | None = None


class _EmbeddingHttpClient(Protocol):
    async def post(self, url: str, *, json: Mapping[str, object]) -> httpx.Response: ...


class OllamaEmbeddingProvider:
    """Batch article embeddings backed by Ollama's ``/api/embed`` endpoint."""

    endpoint_path = '/api/embed'

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: _EmbeddingHttpClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._sleep = sleep or asyncio.sleep

    async def embed_articles(
        self, articles: Sequence[EmbeddingArticle | Mapping[str, object] | object]
    ) -> list[list[float]]:
        """Embed all articles in one request, preserving input order."""

        inputs = [self.build_input(article) for article in articles]
        if not inputs:
            return []
        payload: Mapping[str, object] = {
            'model': self._settings.ollama_embed_model,
            'input': inputs,
            'truncate': False,
        }
        if self._client is not None:
            return await self._request_embeddings(self._client, payload, len(inputs))
        async with self._build_client() as client:
            return await self._request_embeddings(client, payload, len(inputs))

    async def embed(
        self, articles: Sequence[EmbeddingArticle | Mapping[str, object] | object]
    ) -> list[list[float]]:
        """Compatibility alias for callers that use the generic embed verb."""

        return await self.embed_articles(articles)

    def build_input(
        self, article: EmbeddingArticle | Mapping[str, object] | object
    ) -> str:
        """Build and cap one normalized article input without performing I/O."""

        title = normalize_text(self._field(article, 'canonical_title'))
        summary = normalize_text(self._field(article, 'source_summary'))
        body = normalize_text(self._field(article, 'article_body_excerpt'))
        content = summary or body
        value = ' '.join(part for part in (title, content) if part)
        if not value:
            raise OllamaEmbeddingError('invalid article input.')
        return value[: self._settings.similarity_input_chars]

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self._settings.ollama_timeout_seconds)

    async def _request_embeddings(
        self,
        client: _EmbeddingHttpClient,
        payload: Mapping[str, object],
        expected_count: int,
    ) -> list[list[float]]:
        attempts = self._settings.ollama_max_retries + 1
        for attempt in range(attempts):
            try:
                response = await client.post(
                    self._endpoint_url(),
                    json=payload,
                )
            except asyncio.CancelledError:
                raise
            except httpx.NetworkError, httpx.TimeoutException:
                if attempt + 1 < attempts:
                    await self._sleep(0)
                    continue
                raise OllamaEmbeddingError('request failed.') from None

            if self._is_retryable_status(response.status_code):
                if attempt + 1 < attempts:
                    await self._sleep(0)
                    continue
                raise OllamaEmbeddingError('request failed.')
            if response.status_code >= 400:
                raise OllamaEmbeddingError('request failed.')
            return self._parse_embeddings(response, expected_count)

        raise OllamaEmbeddingError('request failed.')

    def _endpoint_url(self) -> str:
        return f'{self._settings.ollama_base_url.rstrip("/")}{self.endpoint_path}'

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        return status_code in {408, 429} or 500 <= status_code <= 599

    @staticmethod
    def _parse_embeddings(
        response: httpx.Response, expected_count: int
    ) -> list[list[float]]:
        try:
            data = response.json()
        except ValueError:
            raise OllamaEmbeddingError('invalid response.') from None
        if not isinstance(data, Mapping):
            raise OllamaEmbeddingError('invalid response.')
        embeddings = data.get('embeddings')
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise OllamaEmbeddingError('invalid response.')

        parsed: list[list[float]] = []
        dimension: int | None = None
        for embedding in embeddings:
            if not isinstance(embedding, list) or not embedding:
                raise OllamaEmbeddingError('invalid response.')
            if dimension is None:
                dimension = len(embedding)
            elif len(embedding) != dimension:
                raise OllamaEmbeddingError('invalid response.')
            values: list[float] = []
            for value in embedding:
                if isinstance(value, bool) or not isinstance(value, Real):
                    raise OllamaEmbeddingError('invalid response.')
                numeric_value = float(value)
                if not math.isfinite(numeric_value):
                    raise OllamaEmbeddingError('invalid response.')
                values.append(numeric_value)
            parsed.append(values)
        return parsed

    @staticmethod
    def _field(
        article: EmbeddingArticle | Mapping[str, object] | object,
        name: str,
    ) -> str | None:
        value: Any
        if isinstance(article, Mapping):
            value = article.get(name)
        else:
            value = getattr(article, name, None)
        return value if isinstance(value, str) else None


__all__ = ['EmbeddingArticle', 'OllamaEmbeddingError', 'OllamaEmbeddingProvider']
