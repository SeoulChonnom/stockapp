from __future__ import annotations

import asyncio
import ssl
from types import SimpleNamespace
from weakref import WeakKeyDictionary

import httpx
import pytest
from langchain_core.messages import AIMessage

from tests.support import load_module

llm_module = load_module('app.core.llm')
settings_module = load_module('app.core.settings')


@pytest.fixture(autouse=True)
def reset_loop_llm_rate_limiters(monkeypatch):
    monkeypatch.setattr(llm_module, '_loop_llm_rate_limiters', WeakKeyDictionary())
    monkeypatch.setattr(llm_module, '_loop_llm_token_limiters', WeakKeyDictionary())


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


@pytest.mark.asyncio
async def test_rate_limiter_allows_burst_then_waits_for_rolling_window():
    clock = FakeClock()
    limiter = llm_module.AsyncSlidingWindowRateLimiter(
        2,
        window_seconds=10.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    acquired_at = []
    for _ in range(3):
        await limiter.acquire()
        acquired_at.append(clock.now)

    assert acquired_at == [0.0, 0.0, 10.0]
    assert clock.sleep_calls == [10.0]


@pytest.mark.asyncio
async def test_rate_limiter_releases_queue_lock_when_waiter_is_cancelled():
    class BlockingSleeper:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def __call__(self, _seconds: float) -> None:
            self.started.set()
            await self.release.wait()

    clock = FakeClock()
    sleeper = BlockingSleeper()
    limiter = llm_module.AsyncSlidingWindowRateLimiter(
        1,
        window_seconds=10.0,
        clock=clock.monotonic,
        sleeper=sleeper,
    )
    await limiter.acquire()

    cancelled_waiter = asyncio.create_task(limiter.acquire())
    await sleeper.started.wait()
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter

    clock.now = 10.0
    await asyncio.wait_for(limiter.acquire(), timeout=0.1)


@pytest.mark.asyncio
async def test_token_limiter_reserves_concurrent_inputs_and_recovers_window():
    clock = FakeClock()
    limiter = llm_module.AsyncSlidingWindowTokenLimiter(
        100,
        window_seconds=10.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )

    first, second = await asyncio.gather(limiter.acquire(60), limiter.acquire(40))
    third = await limiter.acquire(1)

    assert first.token_count == 60
    assert second.token_count == 40
    assert third.timestamp == 10.0
    assert clock.sleep_calls == [10.0]


@pytest.mark.asyncio
async def test_token_limiter_reconciles_estimate_with_actual_usage():
    clock = FakeClock()
    limiter = llm_module.AsyncSlidingWindowTokenLimiter(
        100,
        window_seconds=10.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    reservation = await limiter.acquire(80)

    await limiter.reconcile(reservation, 20)
    await limiter.acquire(80)

    assert reservation.token_count == 20
    assert clock.sleep_calls == []


@pytest.mark.asyncio
async def test_token_limiters_are_scoped_by_project_and_model():
    same_scope = llm_module._get_loop_llm_token_limiter(
        100,
        project_id='project-a',
        model_name='model-a',
    )

    assert (
        llm_module._get_loop_llm_token_limiter(
            100,
            project_id='project-a',
            model_name='model-a',
        )
        is same_scope
    )
    assert (
        llm_module._get_loop_llm_token_limiter(
            100,
            project_id='project-b',
            model_name='model-a',
        )
        is not same_scope
    )
    assert (
        llm_module._get_loop_llm_token_limiter(
            100,
            project_id='project-a',
            model_name='model-b',
        )
        is not same_scope
    )


@pytest.mark.asyncio
async def test_gemini_client_reserves_estimate_and_reconciles_actual_usage(monkeypatch):
    class NoopRateLimiter:
        async def acquire(self) -> None:
            return None

    class RecordingTokenLimiter:
        def __init__(self) -> None:
            self.estimated: list[int] = []
            self.reconciled: list[tuple[int, int]] = []

        async def acquire(self, token_count: int):
            self.estimated.append(token_count)
            return llm_module.TokenReservation(0.0, token_count)

        async def reconcile(self, reservation, actual_token_count: int) -> None:
            self.reconciled.append((reservation.token_count, actual_token_count))

    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                content='{"ok": true}',
                usage_metadata={'input_tokens': 37},
            )

    token_limiter = RecordingTokenLimiter()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key'),
        rate_limiter=NoopRateLimiter(),
        token_limiter=token_limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    result = await client.invoke_json(system_prompt='system', user_prompt='사용자 입력')

    expected_estimate = llm_module.estimate_input_tokens('system', '사용자 입력')
    assert result == {'ok': True}
    assert token_limiter.estimated == [expected_estimate]
    assert token_limiter.reconciled == [(expected_estimate, 37)]


@pytest.mark.asyncio
async def test_gemini_json_client_returns_controlled_timeout(monkeypatch):
    class HangingModel:
        async def ainvoke(self, _messages):
            await asyncio.sleep(1)
            return SimpleNamespace(content='{"ok": true}')

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=0,
            llm_timeout_seconds=0.01,
        )
    )
    monkeypatch.setattr(client, '_build_model', lambda: HangingModel())

    with pytest.raises(llm_module.LlmTimeoutError, match='timed out'):
        await client.invoke_json(system_prompt='system', user_prompt='user')


@pytest.mark.asyncio
async def test_gemini_json_client_waits_for_rate_limit_before_attempt_timeout(
    monkeypatch,
):
    class GateLimiter:
        def __init__(self) -> None:
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def acquire(self) -> None:
            self.entered.set()
            await self.release.wait()

    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"ok": true}')

    limiter = GateLimiter()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_timeout_seconds=0.001,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    invocation = asyncio.create_task(
        client.invoke_json(system_prompt='system', user_prompt='user')
    )
    await limiter.entered.wait()
    await asyncio.sleep(0.01)
    assert invocation.done() is False

    limiter.release.set()
    assert await invocation == {'ok': True}


@pytest.mark.asyncio
async def test_gemini_json_clients_share_event_loop_rate_limit(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"ok": true}')

    clock = FakeClock()
    shared_limiter = llm_module.AsyncSlidingWindowRateLimiter(
        2,
        window_seconds=10.0,
        clock=clock.monotonic,
        sleeper=clock.sleep,
    )
    running_loop = asyncio.get_running_loop()
    monkeypatch.setitem(
        llm_module._loop_llm_rate_limiters,
        running_loop,
        {('default', settings_module.Settings().llm_model): (2, shared_limiter)},
    )
    settings = settings_module.Settings(
        app_env='development',
        gemini_api_key='test-key',
        llm_requests_per_minute=2,
    )
    first_client = llm_module.GeminiJsonClient(settings)
    second_client = llm_module.GeminiJsonClient(settings)
    monkeypatch.setattr(first_client, '_build_model', lambda: RespondingModel())
    monkeypatch.setattr(second_client, '_build_model', lambda: RespondingModel())

    await first_client.invoke_json(system_prompt='system', user_prompt='first')
    await second_client.invoke_json(system_prompt='system', user_prompt='second')
    await first_client.invoke_json(system_prompt='system', user_prompt='third')

    assert (
        llm_module._get_loop_llm_rate_limiter(
            2,
            project_id=settings.llm_quota_project_id,
            model_name=settings.llm_model,
        )
        is shared_limiter
    )
    assert clock.sleep_calls == [10.0]


def test_gemini_rate_limiter_is_scoped_to_the_running_event_loop():
    async def get_rate_limiter():
        return llm_module._get_loop_llm_rate_limiter(2)

    first_loop = asyncio.new_event_loop()
    second_loop = asyncio.new_event_loop()
    try:
        first_limiter = first_loop.run_until_complete(get_rate_limiter())
        second_limiter = second_loop.run_until_complete(get_rate_limiter())
    finally:
        first_loop.close()
        second_loop.close()

    assert first_limiter is not second_limiter


@pytest.mark.asyncio
async def test_gemini_rate_limiter_rejects_mismatched_config_in_same_loop():
    llm_module._get_loop_llm_rate_limiter(2)

    with pytest.raises(
        llm_module.LlmConfigurationError,
        match='one event loop',
    ):
        llm_module._get_loop_llm_rate_limiter(3)


def test_gemini_retry_after_reads_headers_and_retry_info():
    provider_error = RuntimeError('provider throttled')
    provider_error.response = SimpleNamespace(headers={'Retry-After': '999'})
    retry_info_error = RuntimeError('provider throttled')
    retry_info_error.error_details = [
        SimpleNamespace(retry_delay=SimpleNamespace(seconds=7, nanos=500_000_000))
    ]
    sdk_error = RuntimeError('provider throttled')
    sdk_error.details = {
        'error': {
            'details': [
                {
                    '@type': 'type.googleapis.com/google.rpc.RetryInfo',
                    'retryDelay': '12.25s',
                }
            ]
        }
    }

    assert llm_module._retry_after_seconds(provider_error) == 999.0
    assert llm_module._retry_after_seconds(retry_info_error) == 7.5
    assert llm_module._retry_after_seconds(sdk_error) == 12.25


@pytest.mark.asyncio
async def test_gemini_json_client_surfaces_transient_error_without_sleep(monkeypatch):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class ProviderError(Exception):
        def __init__(self, status_code: int, retry_after: str | None = None) -> None:
            self.code = status_code
            self.response = SimpleNamespace(
                headers={'Retry-After': retry_after} if retry_after else {}
            )
            super().__init__(f'provider status {status_code}')

    class FailingModel:
        def __init__(self) -> None:
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            try:
                raise ProviderError(429, retry_after='3')
            except ProviderError as exc:
                raise RuntimeError('wrapped provider error') from exc

    limiter = CountingLimiter()
    model = FailingModel()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: model)

    with pytest.raises(llm_module.LlmRetryableError) as exc_info:
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert exc_info.value.retry_after_seconds == 3.0
    assert model.call_count == 1
    assert limiter.acquire_count == 1


@pytest.mark.asyncio
async def test_gemini_json_client_surfaces_timeout_for_durable_retry(
    monkeypatch,
):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class TimeoutModel:
        def __init__(self) -> None:
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            await asyncio.sleep(1)

    limiter = CountingLimiter()
    model = TimeoutModel()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=1,
            llm_timeout_seconds=0.001,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: model)

    with pytest.raises(llm_module.LlmTimeoutError):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert model.call_count == 1
    assert limiter.acquire_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_error',
    [
        httpx.ConnectError('connection reset'),
        httpx.ReadTimeout('read timed out'),
    ],
    ids=['connect-error', 'read-timeout'],
)
async def test_gemini_json_client_defers_transient_transport_errors(
    transport_error,
    monkeypatch,
):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class FailingTransportModel:
        def __init__(self) -> None:
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            try:
                raise transport_error
            except httpx.TransportError as exc:
                raise RuntimeError('wrapped transport error') from exc

    limiter = CountingLimiter()
    model = FailingTransportModel()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=1,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: model)

    with pytest.raises(llm_module.LlmRetryableError):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert model.call_count == 1
    assert limiter.acquire_count == 1


@pytest.mark.asyncio
async def test_gemini_json_client_converts_final_transient_error_to_fallback_input(
    monkeypatch,
):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class FailingTransportModel:
        def __init__(self) -> None:
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            raise httpx.ConnectError('connection reset')

    limiter = CountingLimiter()
    model = FailingTransportModel()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=1,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: model)

    with llm_module.llm_retry_exhausted_mode():
        with pytest.raises(llm_module.LlmRetryExhaustedError):
            await client.invoke_json(system_prompt='system', user_prompt='user')

    assert model.call_count == 1
    assert limiter.acquire_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_error',
    [
        httpx.UnsupportedProtocol('unsupported URL protocol'),
        httpx.LocalProtocolError('invalid request framing'),
        httpx.ConnectError('TLS certificate verification failed'),
    ],
    ids=['unsupported-protocol', 'local-protocol', 'tls-certificate'],
)
async def test_gemini_json_client_does_not_retry_permanent_transport_errors(
    transport_error,
    monkeypatch,
):
    if isinstance(transport_error, httpx.ConnectError):
        try:
            raise ssl.SSLCertVerificationError('certificate verify failed')
        except ssl.SSLCertVerificationError as exc:
            transport_error.__cause__ = exc

    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class FailingTransportModel:
        def __init__(self) -> None:
            self.call_count = 0

        async def ainvoke(self, _messages):
            self.call_count += 1
            raise transport_error

    limiter = CountingLimiter()
    model = FailingTransportModel()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: model)

    with pytest.raises(type(transport_error)):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert model.call_count == 1
    assert limiter.acquire_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('status_code', [400, 401, 403, 404])
async def test_gemini_json_client_does_not_retry_permanent_provider_errors(
    status_code,
    monkeypatch,
):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class ProviderError(Exception):
        def __init__(self) -> None:
            self.code = status_code
            super().__init__(f'provider status {status_code}')

    class FailingModel:
        async def ainvoke(self, _messages):
            raise ProviderError()

    limiter = CountingLimiter()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: FailingModel())

    with pytest.raises(ProviderError):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert limiter.acquire_count == 1


@pytest.mark.asyncio
async def test_gemini_json_client_does_not_retry_validation_errors(monkeypatch):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class InvalidRequestModel:
        async def ainvoke(self, _messages):
            raise ValueError('invalid provider request')

    limiter = CountingLimiter()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: InvalidRequestModel())

    with pytest.raises(ValueError, match='invalid provider request'):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert limiter.acquire_count == 1


@pytest.mark.asyncio
async def test_gemini_json_client_does_not_retry_json_parse_errors(monkeypatch):
    class CountingLimiter:
        def __init__(self) -> None:
            self.acquire_count = 0

        async def acquire(self) -> None:
            self.acquire_count += 1

    class InvalidJsonModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='not-json')

    limiter = CountingLimiter()
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        ),
        rate_limiter=limiter,
    )
    monkeypatch.setattr(client, '_build_model', lambda: InvalidJsonModel())

    with pytest.raises(ValueError):
        await client.invoke_json(system_prompt='system', user_prompt='user')

    assert limiter.acquire_count == 1


def test_gemini_json_client_disables_provider_internal_retries(monkeypatch):
    captured_kwargs = {}

    class FakeModel:
        def __init__(self, **kwargs) -> None:
            captured_kwargs.update(kwargs)

    monkeypatch.setattr(llm_module, 'ChatGoogleGenerativeAI', FakeModel)
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            gemini_api_key='test-key',
            llm_max_retries=2,
        )
    )

    client._build_model()

    assert captured_kwargs['max_retries'] == 0


@pytest.mark.asyncio
async def test_gemini_json_client_parses_json_when_model_responds(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(content='{"ok": true}')

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.asyncio
async def test_gemini_json_client_parses_structured_text_blocks(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                content=[
                    {
                        'type': 'text',
                        'text': '```json\n{"ok": true}\n```',
                        'extras': {},
                    }
                ]
            )

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.asyncio
async def test_gemini_json_client_combines_text_blocks_from_ai_message(monkeypatch):
    class RespondingModel:
        async def ainvoke(self, _messages):
            return AIMessage(
                content=[
                    {'type': 'image', 'image_url': 'https://example.test/image.png'},
                    {'type': 'text', 'text': '{"ok":'},
                    {'type': 'text', 'text': ' true}'},
                ]
            )

    client = llm_module.GeminiJsonClient(
        settings_module.Settings(app_env='development', gemini_api_key='test-key')
    )
    monkeypatch.setattr(client, '_build_model', lambda: RespondingModel())

    assert await client.invoke_json(system_prompt='system', user_prompt='user') == {
        'ok': True
    }


@pytest.mark.parametrize(
    'content',
    [
        '',
        [],
        [{'type': 'text', 'text': ''}],
        [{'type': 'image', 'image_url': 'https://example.test/image.png'}],
    ],
    ids=[
        'empty-string',
        'empty-block-list',
        'empty-text-block',
        'unsupported-block',
    ],
)
def test_gemini_json_client_rejects_structured_content_without_text(content):
    with pytest.raises(ValueError, match='Expected text content'):
        llm_module.GeminiJsonClient._extract_text_content(content)


def test_gemini_json_client_exposes_configured_model_identity():
    client = llm_module.GeminiJsonClient(
        settings_module.Settings(
            app_env='development',
            llm_model='test-model',
            llm_concurrency_limit=3,
        )
    )

    assert client.model_name == 'test-model'
    assert client.concurrency_limit == 3
