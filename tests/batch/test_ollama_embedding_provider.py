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


class _UnclosedResponseFake:
    def __init__(
        self, status_code: int, payload: object = None, *, invalid_json: bool = False
    ):
        self.status_code = status_code
        self._payload = payload
        self._invalid_json = invalid_json
        self.closed = False

    def json(self) -> object:
        if self._invalid_json:
            raise ValueError('invalid json')
        return self._payload

    async def aclose(self) -> None:
        self.closed = True


class _DirectAsyncClientFake:
    def __init__(self, responses: list[_UnclosedResponseFake]):
        self.responses = responses
        self.calls = 0

    async def post(self, _url: str, *, json: object) -> _UnclosedResponseFake:
        _ = json
        self.calls += 1
        return self.responses.pop(0)


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
async def test_embed_articles_sends_sequential_chunks_and_flattens_original_order():
    requests: list[dict[str, object]] = []
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        first_index = payload['input'][0].split()[1]
        events.append(f'start:{first_index}')
        vectors = [[float(value.split()[1]), 1.0] for value in payload['input']]
        events.append(f'end:{first_index}')
        return httpx.Response(
            200,
            json={'embeddings': vectors},
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(5)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        build_calls = 0
        original_build_input = provider.build_input

        def counting_build_input(article):
            nonlocal build_calls
            build_calls += 1
            return original_build_input(article)

        provider.build_input = counting_build_input
        result = await provider.embed_articles(articles)

    assert [len(payload['input']) for payload in requests] == [2, 2, 1]
    assert [value[0] for value in result] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert events == [
        'start:0',
        'end:0',
        'start:2',
        'end:2',
        'start:4',
        'end:4',
    ]
    assert build_calls == len(articles)


@pytest.mark.anyio
async def test_embed_articles_rejects_dimension_change_between_chunks():
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(payload['input']) == 3:
            dimension = 2
        else:
            dimension = 2 if len(requests) == 1 else 1
        return httpx.Response(
            200,
            json={
                'embeddings': [
                    [float(index) for index in range(dimension)]
                    for _ in payload['input']
                ]
            },
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(3)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='invalid response'):
            await provider.embed_articles(articles)

    assert [len(payload['input']) for payload in requests] == [2, 1]


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
async def test_embed_articles_closes_direct_fake_success_response():
    response = _UnclosedResponseFake(200, {'embeddings': [[1.0]]})
    client = _DirectAsyncClientFake([response])
    provider = OllamaEmbeddingProvider(
        Settings(_env_file=None, ollama_base_url='http://ollama.test'),
        client=client,
    )

    assert await provider.embed_articles([_article()]) == [[1.0]]
    assert response.closed is True
    assert client.calls == 1


@pytest.mark.anyio
async def test_embed_articles_closes_direct_fake_retry_response_before_next_attempt():
    retry_response = _UnclosedResponseFake(503, {'error': 'busy'})
    success_response = _UnclosedResponseFake(200, {'embeddings': [[1.0]]})
    client = _DirectAsyncClientFake([retry_response, success_response])
    provider = OllamaEmbeddingProvider(
        Settings(
            _env_file=None,
            ollama_base_url='http://ollama.test',
            ollama_max_retries=1,
        ),
        client=client,
    )

    assert await provider.embed_articles([_article()]) == [[1.0]]
    assert retry_response.closed is True
    assert success_response.closed is True
    assert client.calls == 2


@pytest.mark.anyio
async def test_embed_articles_closes_direct_fake_permanent_response():
    response = _UnclosedResponseFake(404, {'error': 'missing'})
    client = _DirectAsyncClientFake([response])
    provider = OllamaEmbeddingProvider(
        Settings(
            _env_file=None,
            ollama_base_url='http://ollama.test',
            ollama_max_retries=2,
        ),
        client=client,
    )

    with pytest.raises(OllamaEmbeddingError, match='request failed'):
        await provider.embed_articles([_article()])

    assert response.closed is True
    assert client.calls == 1


@pytest.mark.anyio
@pytest.mark.parametrize('response', ['invalid_json', 'invalid_shape'])
async def test_embed_articles_closes_direct_fake_invalid_response(response: str):
    fake_response = (
        _UnclosedResponseFake(200, invalid_json=True)
        if response == 'invalid_json'
        else _UnclosedResponseFake(200, {'embeddings': [[1.0], [2.0, 3.0]]})
    )
    client = _DirectAsyncClientFake([fake_response])
    provider = OllamaEmbeddingProvider(
        Settings(_env_file=None, ollama_base_url='http://ollama.test'),
        client=client,
    )

    with pytest.raises(OllamaEmbeddingError, match='invalid response'):
        await provider.embed_articles([_article(), _article('두 번째')])

    assert fake_response.closed is True
    assert client.calls == 1


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


@pytest.mark.anyio
@pytest.mark.parametrize(
    ('status_code', 'expected_status'),
    [(404, 404), (401, 401), (503, 503)],
)
async def test_embed_articles_records_the_rejecting_http_status(
    status_code: int,
    expected_status: int,
):
    """The status is what tells a missing model apart from a broken host.

    Every failure otherwise arrives as the same sanitized error, which left an
    outage and a model that was never pulled indistinguishable in both the log
    and the persisted event.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={'error': 'failure'}, request=request)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError) as exc_info:
            await provider.embed_articles([_article()])

    assert exc_info.value.reason == 'http_status'
    assert exc_info.value.status_code == expected_status


@pytest.mark.anyio
@pytest.mark.parametrize(
    ('transport_error', 'expected_reason'),
    [
        (httpx.ConnectError('refused'), 'network_error'),
        (httpx.ReadTimeout('timed out'), 'timeout'),
    ],
)
async def test_embed_articles_distinguishes_transport_failures(
    transport_error: Exception,
    expected_reason: str,
):
    def handler(request: httpx.Request) -> httpx.Response:
        raise transport_error

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError) as exc_info:
            await provider.embed_articles([_article()])

    assert exc_info.value.reason == expected_reason
    assert exc_info.value.status_code is None


@pytest.mark.anyio
async def test_embed_articles_marks_an_unusable_response_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b'not-json', request=request)

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError) as exc_info:
            await provider.embed_articles([_article()])

    assert exc_info.value.reason == 'invalid_response'
