from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from app.batch.providers.ollama_embedding_provider import (
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)
from app.core.settings import Settings


def _article(
    title: str | None = '삼성전자 실적 개선',
    summary: str | None = '영업이익 증가',
    body: str | None = '본문 발췌',
) -> SimpleNamespace:
    return SimpleNamespace(
        canonical_title=title,
        source_summary=summary,
        article_body_excerpt=body,
    )


@pytest.mark.anyio
async def test_embed_articles_posts_one_exact_batch_request():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.path == '/api/embed'
        assert request.content
        assert request.read() == request.content
        assert json.loads(request.content) == {
            'model': 'bge-m3',
            'input': ['삼성전자 실적 개선 영업이익 증가', 'sk 하이닉스 수요 회복'],
            'truncate': False,
        }
        return httpx.Response(
            200,
            json={'embeddings': [[1.0, 0.0], [0.0, 1.0]]},
            request=request,
        )

    settings = Settings(_env_file=None, ollama_base_url='http://ollama.test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles(
            [_article(), _article('SK 하이닉스', None, '수요 회복')]
        )

    assert result == [[1.0, 0.0], [0.0, 1.0]]
    assert len(requests) == 1


@pytest.mark.anyio
async def test_embed_articles_caps_normalized_fallback_input_before_request():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content)
        assert payload['input'] == ['제목 본문이 매우 ']
        assert len(payload['input'][0]) == 10
        return httpx.Response(200, json={'embeddings': [[0.1]]}, request=request)

    settings = Settings(
        _env_file=None,
        similarity_input_chars=10,
        ollama_base_url='http://ollama.test',
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles(
            [_article('  제목\n', None, ' 본문이 매우 길어서 잘립니다 ')]
        )

    assert result == [[0.1]]
    assert len(requests) == 1


@pytest.mark.anyio
async def test_embed_articles_retries_connection_and_transient_http_errors():
    attempts = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError('offline', request=request)
        if attempts == 2:
            return httpx.Response(503, json={'error': 'busy'}, request=request)
        return httpx.Response(200, json={'embeddings': [[1.0]]}, request=request)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client, sleep=sleep)
        result = await provider.embed_articles([_article()])

    assert result == [[1.0]]
    assert attempts == 3
    assert len(sleeps) == 2


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [408, 429, 500, 503])
async def test_embed_articles_retries_each_transient_status_once(status_code: int):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(status_code, content=b'busy', request=request)
        return httpx.Response(200, json={'embeddings': [[0.25]]}, request=request)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles([_article()])

    assert result == [[0.25]]
    assert attempts == 2


@pytest.mark.anyio
async def test_embed_articles_retries_timeout_once():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout('timed out', request=request)
        return httpx.Response(200, json={'embeddings': [[0.5]]}, request=request)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=1,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles([_article()])

    assert result == [[0.5]]
    assert attempts == 2


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [400, 401, 404])
async def test_embed_articles_does_not_retry_permanent_http_errors(status_code: int):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(status_code, json={'error': 'failure'}, request=request)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='request failed'):
            await provider.embed_articles([_article()])

    assert attempts == 1


@pytest.mark.anyio
@pytest.mark.parametrize('status_code', [100, 199, 300, 301, 399])
async def test_embed_articles_rejects_non_success_http_status_without_retry(
    status_code: int,
):
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            status_code,
            json={'embeddings': [[1.0]]},
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='request failed'):
            await provider.embed_articles([_article()])

    assert attempts == 1


@pytest.mark.anyio
async def test_embed_articles_rejects_invalid_json_shape_count_dimension_and_values():
    responses = [
        httpx.Response(200, content=b'not-json'),
        httpx.Response(200, json={'embeddings': [[1.0]]}),
        httpx.Response(200, json={'embeddings': [[1.0], [2.0, 3.0]]}),
        httpx.Response(200, json={'embeddings': [[True], [0.0]]}),
        httpx.Response(200, content=b'{"embeddings":[[NaN],[0.0]]}'),
    ]

    for response in responses:

        def handler(request: httpx.Request, response=response) -> httpx.Response:
            response.request = request
            return response

        settings = Settings(_env_file=None, ollama_base_url='http://ollama.test')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OllamaEmbeddingProvider(settings, client=client)
            with pytest.raises(OllamaEmbeddingError, match='invalid response'):
                await provider.embed_articles([_article(), _article('두 번째')])


@pytest.mark.anyio
async def test_embed_articles_sanitizes_float_conversion_overflow():
    huge_integer = 10**1000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={'embeddings': [[huge_integer]]},
            request=request,
        )

    settings = Settings(_env_file=None, ollama_base_url='http://ollama.test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='invalid response') as exc_info:
            await provider.embed_articles([_article()])

    assert 'http://ollama.test' not in str(exc_info.value)
    assert '100000' not in str(exc_info.value)


@pytest.mark.anyio
async def test_embed_articles_preserves_non_unit_vector_values():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={'embeddings': [[3.0, 4.0]]},
            request=request,
        )

    settings = Settings(_env_file=None, ollama_base_url='http://ollama.test')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles([_article()])

    assert result == [[3.0, 4.0]]


@pytest.mark.anyio
async def test_embed_articles_closes_success_response_without_closing_injected_client():
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, json={'embeddings': [[1.0]]}, request=request)
        responses.append(response)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        Settings(_env_file=None, ollama_base_url='http://ollama.test'),
        client=client,
    )
    await provider.embed_articles([_article()])

    assert responses[0].is_closed is True
    assert client.is_closed is False
    await client.aclose()


@pytest.mark.anyio
async def test_embed_articles_closes_retry_response():
    responses: list[httpx.Response] = []
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        response = (
            httpx.Response(503, content=b'busy', request=request)
            if attempts == 1
            else httpx.Response(200, json={'embeddings': [[1.0]]}, request=request)
        )
        responses.append(response)
        return response

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        Settings(
            _env_file=None,
            ollama_base_url='http://ollama.test',
            ollama_max_retries=1,
        ),
        client=client,
    )
    await provider.embed_articles([_article()])

    assert [response.is_closed for response in responses] == [True, True]
    await client.aclose()


@pytest.mark.anyio
async def test_embed_articles_closes_internally_owned_client(monkeypatch):
    responses: list[httpx.Response] = []

    def handler(request: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, json={'embeddings': [[1.0]]}, request=request)
        responses.append(response)
        return response

    internal_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    settings = Settings(_env_file=None, ollama_base_url='http://ollama.test')
    provider = OllamaEmbeddingProvider(settings)
    monkeypatch.setattr(provider, '_build_client', lambda: internal_client)

    await provider.embed_articles([_article()])

    assert responses[0].is_closed is True
    assert internal_client.is_closed is True


@pytest.mark.anyio
async def test_embed_articles_propagates_cancellation_without_retry():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=2,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(asyncio.CancelledError):
            await provider.embed_articles([_article()])

    assert attempts == 1


@pytest.mark.anyio
async def test_embed_articles_does_not_close_injected_client():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={'embeddings': [[1.0]]}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaEmbeddingProvider(
        Settings(_env_file=None, ollama_base_url='http://ollama.test'),
        client=client,
    )
    await provider.embed_articles([_article()])
    assert client.is_closed is False
    await client.aclose()
