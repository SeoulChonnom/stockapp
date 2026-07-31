from __future__ import annotations

import asyncio
import json
import math
import ssl
from collections import deque
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol
from weakref import WeakKeyDictionary

import httpx
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.settings import Settings, get_settings


class LlmConfigurationError(RuntimeError):
    pass


class LlmRetryableError(RuntimeError):
    """Signal that the durable worker should retry this invocation later."""

    def __init__(self, *, retry_after_seconds: float | None = None) -> None:
        super().__init__('Temporary LLM provider failure.')
        self.retry_after_seconds = retry_after_seconds


class LlmTimeoutError(LlmRetryableError):
    def __init__(self, message: str = 'LLM invocation timed out.') -> None:
        RuntimeError.__init__(self, message)
        self.retry_after_seconds = None


class LlmRetryExhaustedError(RuntimeError):
    """Sanitized terminal error used to preserve deterministic fallbacks."""


class _AsyncRateLimiter(Protocol):
    async def acquire(self) -> None: ...


class _AsyncTokenLimiter(Protocol):
    async def acquire(self, token_count: int) -> TokenReservation: ...

    async def reconcile(
        self,
        reservation: TokenReservation,
        actual_token_count: int,
    ) -> None: ...


_RETRY_AFTER_MAX_SECONDS = 3600.0
_INPUT_TOKEN_BYTES_PER_TOKEN = 4
_INPUT_TOKEN_MESSAGE_OVERHEAD = 16
_llm_retry_exhausted: ContextVar[bool] = ContextVar(
    'llm_retry_exhausted',
    default=False,
)


@contextmanager
def llm_retry_exhausted_mode() -> Iterator[None]:
    """Convert transient provider failures to terminal fallback inputs."""
    token = _llm_retry_exhausted.set(True)
    try:
        yield
    finally:
        _llm_retry_exhausted.reset(token)


@dataclass(slots=True)
class TokenReservation:
    timestamp: float
    token_count: int


class AsyncSlidingWindowRateLimiter:
    """Limit async requests over a rolling time window."""

    def __init__(
        self,
        requests_per_window: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if requests_per_window < 1:
            raise ValueError('requests_per_window must be at least 1.')
        if window_seconds <= 0:
            raise ValueError('window_seconds must be greater than 0.')
        self._requests_per_window = requests_per_window
        self._window_seconds = window_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._request_times: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request slot is available and claim it."""
        async with self._lock:
            while True:
                now = self._clock()
                cutoff = now - self._window_seconds
                while self._request_times and self._request_times[0] <= cutoff:
                    self._request_times.popleft()

                if len(self._request_times) < self._requests_per_window:
                    self._request_times.append(now)
                    return

                wait_seconds = self._request_times[0] + self._window_seconds - now
                await self._sleeper(max(wait_seconds, 0.0))


class AsyncSlidingWindowTokenLimiter:
    """Reserve estimated input tokens and reconcile provider usage metadata."""

    def __init__(
        self,
        tokens_per_window: int,
        *,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if tokens_per_window < 1:
            raise ValueError('tokens_per_window must be at least 1.')
        if window_seconds <= 0:
            raise ValueError('window_seconds must be greater than 0.')
        self._tokens_per_window = tokens_per_window
        self._window_seconds = window_seconds
        self._clock = clock
        self._sleeper = sleeper
        self._reservations: deque[TokenReservation] = deque()
        self._reserved_tokens = 0
        self._lock = asyncio.Lock()

    async def acquire(self, token_count: int) -> TokenReservation:
        if token_count < 1:
            raise ValueError('token_count must be at least 1.')
        if token_count > self._tokens_per_window:
            raise LlmConfigurationError(
                'Estimated LLM input tokens exceed the configured TPM limit.'
            )
        async with self._lock:
            while True:
                now = self._clock()
                self._prune(now)
                if self._reserved_tokens + token_count <= self._tokens_per_window:
                    reservation = TokenReservation(now, token_count)
                    self._reservations.append(reservation)
                    self._reserved_tokens += token_count
                    return reservation

                wait_seconds = (
                    self._reservations[0].timestamp + self._window_seconds - now
                )
                await self._sleeper(max(wait_seconds, 0.0))

    async def reconcile(
        self,
        reservation: TokenReservation,
        actual_token_count: int,
    ) -> None:
        if actual_token_count < 0:
            return
        async with self._lock:
            self._prune(self._clock())
            if reservation not in self._reservations:
                return
            self._reserved_tokens += actual_token_count - reservation.token_count
            reservation.token_count = actual_token_count

    def _prune(self, now: float) -> None:
        cutoff = now - self._window_seconds
        while self._reservations and self._reservations[0].timestamp <= cutoff:
            expired = self._reservations.popleft()
            self._reserved_tokens -= expired.token_count


_loop_llm_rate_limiters: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[tuple[str, str], tuple[int, AsyncSlidingWindowRateLimiter]],
] = WeakKeyDictionary()
_loop_llm_token_limiters: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[tuple[str, str], tuple[int, AsyncSlidingWindowTokenLimiter]],
] = WeakKeyDictionary()


def _get_loop_llm_rate_limiter(
    requests_per_minute: int,
    *,
    project_id: str = 'default',
    model_name: str = 'default',
) -> AsyncSlidingWindowRateLimiter:
    loop = asyncio.get_running_loop()
    scoped_limiters = _loop_llm_rate_limiters.setdefault(loop, {})
    scope = (project_id, model_name)
    existing = scoped_limiters.get(scope)
    if existing is None:
        rate_limiter = AsyncSlidingWindowRateLimiter(requests_per_minute)
        scoped_limiters[scope] = (requests_per_minute, rate_limiter)
        return rate_limiter

    configured_requests_per_minute, rate_limiter = existing
    if configured_requests_per_minute != requests_per_minute:
        raise LlmConfigurationError(
            'LLM requests-per-minute must remain consistent within one event loop.'
        )
    return rate_limiter


def _get_loop_llm_token_limiter(
    tokens_per_minute: int,
    *,
    project_id: str = 'default',
    model_name: str = 'default',
) -> AsyncSlidingWindowTokenLimiter:
    loop = asyncio.get_running_loop()
    scoped_limiters = _loop_llm_token_limiters.setdefault(loop, {})
    scope = (project_id, model_name)
    existing = scoped_limiters.get(scope)
    if existing is None:
        token_limiter = AsyncSlidingWindowTokenLimiter(tokens_per_minute)
        scoped_limiters[scope] = (tokens_per_minute, token_limiter)
        return token_limiter

    configured_tokens_per_minute, token_limiter = existing
    if configured_tokens_per_minute != tokens_per_minute:
        raise LlmConfigurationError(
            'LLM tokens-per-minute must remain consistent for one '
            'project/model scope within an event loop.'
        )
    return token_limiter


def _exception_chain(exc: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _provider_status_code(exc: BaseException) -> int | None:
    for current in _exception_chain(exc):
        for value in (
            getattr(current, 'code', None),
            getattr(current, 'status_code', None),
            getattr(getattr(current, 'response', None), 'status_code', None),
        ):
            if isinstance(value, int):
                return value
    return None


def _retry_after_seconds(exc: BaseException) -> float | None:
    for current in _exception_chain(exc):
        headers = getattr(getattr(current, 'response', None), 'headers', None)
        if isinstance(headers, Mapping):
            raw_retry_after = headers.get('Retry-After') or headers.get('retry-after')
            if raw_retry_after is not None:
                try:
                    retry_after = float(raw_retry_after)
                except TypeError, ValueError:
                    pass
                else:
                    if retry_after >= 0:
                        return min(retry_after, _RETRY_AFTER_MAX_SECONDS)

        for detail in _provider_error_details(current):
            retry_delay = (
                getattr(detail, 'retry_delay', None)
                or (detail.get('retryDelay') if isinstance(detail, Mapping) else None)
                or (detail.get('retry_delay') if isinstance(detail, Mapping) else None)
            )
            parsed_delay = _duration_seconds(retry_delay)
            if parsed_delay is not None:
                return min(parsed_delay, _RETRY_AFTER_MAX_SECONDS)
    return None


def _provider_error_details(exc: BaseException) -> list[Any]:
    details: list[Any] = []
    for value in (
        getattr(exc, 'details', None),
        getattr(exc, 'error_details', None),
        getattr(getattr(exc, 'response', None), 'error_details', None),
    ):
        if isinstance(value, (list, tuple)):
            details.extend(value)
        elif isinstance(value, Mapping):
            nested = value.get('details')
            if isinstance(nested, list):
                details.extend(nested)
            nested_error = value.get('error')
            if isinstance(nested_error, Mapping):
                nested_details = nested_error.get('details')
                if isinstance(nested_details, list):
                    details.extend(nested_details)
    return details


def _duration_seconds(value: object) -> float | None:
    if isinstance(value, (int, float)) and value >= 0:
        return float(value)
    if isinstance(value, str):
        normalized = value.removesuffix('s')
        try:
            parsed = float(normalized)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    seconds = getattr(value, 'seconds', None)
    nanos = getattr(value, 'nanos', 0)
    if isinstance(seconds, int) and isinstance(nanos, int) and seconds >= 0:
        return seconds + (nanos / 1_000_000_000)
    return None


def _is_retryable_provider_error(exc: BaseException) -> bool:
    status_code = _provider_status_code(exc)
    if status_code == 429 or (status_code is not None and 500 <= status_code <= 599):
        return True

    exception_chain = _exception_chain(exc)
    if any(
        isinstance(current, (ssl.CertificateError, ssl.SSLCertVerificationError))
        for current in exception_chain
    ):
        return False

    return any(
        isinstance(current, httpx.TransportError)
        and not isinstance(
            current,
            (httpx.LocalProtocolError, httpx.UnsupportedProtocol),
        )
        for current in exception_chain
    )


def estimate_input_tokens(system_prompt: str, user_prompt: str) -> int:
    """Conservatively estimate input tokens without an extra provider call."""
    prompt = f'{system_prompt}{user_prompt}'
    byte_estimate = math.ceil(
        len(prompt.encode('utf-8')) / _INPUT_TOKEN_BYTES_PER_TOKEN
    )
    multilingual_estimate = len(prompt)
    return max(1, byte_estimate, multilingual_estimate) + _INPUT_TOKEN_MESSAGE_OVERHEAD


def _actual_input_tokens(response: object) -> int | None:
    usage_candidates = [
        getattr(response, 'usage_metadata', None),
        getattr(response, 'response_metadata', None),
    ]
    response_metadata = getattr(response, 'response_metadata', None)
    if isinstance(response_metadata, Mapping):
        usage_candidates.append(response_metadata.get('usage_metadata'))
    for usage in usage_candidates:
        if not isinstance(usage, Mapping):
            continue
        for key in (
            'input_tokens',
            'prompt_token_count',
            'inputTokenCount',
            'promptTokenCount',
        ):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                return value
    return None


class GeminiJsonClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        rate_limiter: _AsyncRateLimiter | None = None,
        token_limiter: _AsyncTokenLimiter | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._rate_limiter = rate_limiter
        self._token_limiter = token_limiter

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
            max_retries=0,
        )

    async def invoke_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        model = self._build_model()
        rate_limiter = self._rate_limiter
        if rate_limiter is None:
            rate_limiter = _get_loop_llm_rate_limiter(
                self._settings.llm_requests_per_minute,
                project_id=self._settings.llm_quota_project_id,
                model_name=self._settings.llm_model,
            )
        token_limiter = self._token_limiter
        if token_limiter is None:
            token_limiter = _get_loop_llm_token_limiter(
                self._settings.llm_tokens_per_minute,
                project_id=self._settings.llm_quota_project_id,
                model_name=self._settings.llm_model,
            )

        messages = [
            ('system', system_prompt),
            ('human', user_prompt),
        ]
        estimated_tokens = estimate_input_tokens(system_prompt, user_prompt)
        await rate_limiter.acquire()
        reservation = await token_limiter.acquire(estimated_tokens)
        try:
            response = await asyncio.wait_for(
                model.ainvoke(messages),
                timeout=self._settings.llm_timeout_seconds,
            )
        except TimeoutError as exc:
            if _llm_retry_exhausted.get():
                raise LlmRetryExhaustedError(
                    'LLM retries were exhausted after a timeout.'
                ) from exc
            raise LlmTimeoutError('LLM invocation timed out.') from exc
        except Exception as exc:
            if not _is_retryable_provider_error(exc):
                raise
            if _llm_retry_exhausted.get():
                raise LlmRetryExhaustedError(
                    'LLM retries were exhausted after a transient provider failure.'
                ) from exc
            raise LlmRetryableError(
                retry_after_seconds=_retry_after_seconds(exc)
            ) from exc
        actual_tokens = _actual_input_tokens(response)
        if actual_tokens is not None:
            await token_limiter.reconcile(reservation, actual_tokens)
        return self._parse_json(self._extract_text_content(response.content))

    @staticmethod
    def _extract_text_content(content: object) -> str:
        if isinstance(content, str):
            if not content.strip():
                raise ValueError('Expected text content from the LLM response.')
            return content

        blocks = content if isinstance(content, list) else [content]
        text_blocks: list[str] = []
        for block in blocks:
            if isinstance(block, str):
                text_blocks.append(block)
                continue
            if isinstance(block, dict):
                text = block.get('text')
                if isinstance(text, str):
                    text_blocks.append(text)

        if not text_blocks:
            raise ValueError('Expected text content from the LLM response.')
        text_content = ''.join(text_blocks)
        if not text_content.strip():
            raise ValueError('Expected text content from the LLM response.')
        return text_content

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


__all__ = [
    'AsyncSlidingWindowRateLimiter',
    'AsyncSlidingWindowTokenLimiter',
    'GeminiJsonClient',
    'LlmConfigurationError',
    'LlmRetryExhaustedError',
    'LlmRetryableError',
    'LlmTimeoutError',
    'TokenReservation',
    'estimate_input_tokens',
    'llm_retry_exhausted_mode',
]
