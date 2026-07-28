from __future__ import annotations

import asyncio
import json
import ssl
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from time import monotonic
from typing import Any, Protocol
from weakref import WeakKeyDictionary

import httpx
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.settings import Settings, get_settings


class LlmConfigurationError(RuntimeError):
    pass


class LlmTimeoutError(RuntimeError):
    pass


class _AsyncRateLimiter(Protocol):
    async def acquire(self) -> None: ...


_RETRY_BACKOFF_BASE_SECONDS = 1.0
_RETRY_BACKOFF_MAX_SECONDS = 8.0
_RETRY_AFTER_MAX_SECONDS = 60.0


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


_loop_llm_rate_limiters: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    tuple[int, AsyncSlidingWindowRateLimiter],
] = WeakKeyDictionary()


def _get_loop_llm_rate_limiter(
    requests_per_minute: int,
) -> AsyncSlidingWindowRateLimiter:
    loop = asyncio.get_running_loop()
    existing = _loop_llm_rate_limiters.get(loop)
    if existing is None:
        rate_limiter = AsyncSlidingWindowRateLimiter(requests_per_minute)
        _loop_llm_rate_limiters[loop] = (requests_per_minute, rate_limiter)
        return rate_limiter

    configured_requests_per_minute, rate_limiter = existing
    if configured_requests_per_minute != requests_per_minute:
        raise LlmConfigurationError(
            'LLM requests-per-minute must remain consistent within one event loop.'
        )
    return rate_limiter


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
        if not isinstance(headers, Mapping):
            continue
        raw_retry_after = headers.get('Retry-After') or headers.get('retry-after')
        if raw_retry_after is None:
            continue
        try:
            retry_after = float(raw_retry_after)
        except TypeError, ValueError:
            continue
        if retry_after >= 0:
            return min(retry_after, _RETRY_AFTER_MAX_SECONDS)
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


def _retry_delay_seconds(exc: BaseException, retry_number: int) -> float:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return retry_after
    return min(
        _RETRY_BACKOFF_BASE_SECONDS * (2 ** max(retry_number - 1, 0)),
        _RETRY_BACKOFF_MAX_SECONDS,
    )


class GeminiJsonClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        rate_limiter: _AsyncRateLimiter | None = None,
        retry_sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._settings = settings or get_settings()
        self._rate_limiter = rate_limiter
        self._retry_sleeper = retry_sleeper

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
                self._settings.llm_requests_per_minute
            )

        messages = [
            ('system', system_prompt),
            ('human', user_prompt),
        ]
        for attempt_number in range(self._settings.llm_max_retries + 1):
            await rate_limiter.acquire()
            try:
                response = await asyncio.wait_for(
                    model.ainvoke(messages),
                    timeout=self._settings.llm_timeout_seconds,
                )
            except TimeoutError as exc:
                if attempt_number >= self._settings.llm_max_retries:
                    raise LlmTimeoutError('LLM invocation timed out.') from exc
                await self._retry_sleeper(_retry_delay_seconds(exc, attempt_number + 1))
            except Exception as exc:
                if (
                    not _is_retryable_provider_error(exc)
                    or attempt_number >= self._settings.llm_max_retries
                ):
                    raise
                await self._retry_sleeper(_retry_delay_seconds(exc, attempt_number + 1))
            else:
                return self._parse_json(self._extract_text_content(response.content))
        raise AssertionError('LLM retry loop exited without a response.')

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
    'GeminiJsonClient',
    'LlmConfigurationError',
    'LlmTimeoutError',
]
