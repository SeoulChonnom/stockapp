from __future__ import annotations

import asyncio
import json
import logging
import math
import random
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

from app.batch.logging import log_safe_exception
from app.core.settings import Settings, get_settings

LOGGER = logging.getLogger(__name__)


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
_CONFIGURATION_STATUS_CODES = frozenset({401, 403, 404})
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


def _is_rate_limited_provider_error(exc: BaseException) -> bool:
    """Report a quota rejection, which retrying immediately would only worsen."""
    return _provider_status_code(exc) == 429


def _is_transient_provider_error(exc: BaseException) -> bool:
    """Report a provider-side blip that usually clears within seconds.

    A 5xx or a transport failure says the request never got a verdict, so
    reissuing it shortly is both safe and usually sufficient. This is kept
    apart from a 429 so the two can be answered differently.
    """
    status_code = _provider_status_code(exc)
    if status_code is not None and 500 <= status_code <= 599:
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


def _is_retryable_provider_error(exc: BaseException) -> bool:
    return _is_rate_limited_provider_error(exc) or _is_transient_provider_error(exc)


def _is_configuration_provider_error(exc: BaseException) -> bool:
    """Report a verdict on how the request is configured rather than on its timing.

    An unknown model, a rejected key, or a forbidden project answers every
    request in the run identically, so reissuing any of them is pointless. A
    400 is deliberately excluded: it can name one malformed payload rather than
    the deployment, and one bad prompt must not silence the whole run.
    """
    return _provider_status_code(exc) in _CONFIGURATION_STATUS_CODES


class LlmConfigurationErrorCircuit:
    """Stop issuing calls a provider keeps rejecting for a configuration reason.

    Left unchecked a misconfigured deployment spends the batch's entire runtime
    queueing doomed requests behind the per-minute limiter -- one production run
    burned over eight minutes to fail every call with the same 404. The circuit
    opens after a few consecutive rejections and lets a single probe through
    once the reset window passes, so a corrected deployment recovers on its own
    instead of needing the process restarted.
    """

    def __init__(
        self,
        *,
        threshold: int,
        reset_seconds: float,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._threshold = threshold
        self._reset_seconds = reset_seconds
        self._clock = clock
        self._consecutive_rejections = 0
        self._opened_at: float | None = None

    def allow_request(self) -> bool:
        """Report whether a call may be issued, half-opening once time has passed."""
        if self._opened_at is None:
            return True
        if self._clock() - self._opened_at < self._reset_seconds:
            return False
        # Half-open: admit one probe. It is left one rejection short of the
        # threshold so a still-broken deployment re-opens on that probe alone.
        self._opened_at = None
        self._consecutive_rejections = self._threshold - 1
        return True

    def record_success(self) -> None:
        self._consecutive_rejections = 0
        self._opened_at = None

    def record_rejection(self) -> bool:
        """Record a rejection and report whether it is the one that opened."""
        self._consecutive_rejections += 1
        if (
            self._opened_at is not None
            or self._consecutive_rejections < self._threshold
        ):
            return False
        self._opened_at = self._clock()
        return True


def build_llm_configuration_error_circuit(
    settings: Settings,
    *,
    clock: Callable[[], float] = monotonic,
) -> LlmConfigurationErrorCircuit:
    """Build a circuit configured from settings."""
    return LlmConfigurationErrorCircuit(
        threshold=settings.llm_config_error_circuit_threshold,
        reset_seconds=settings.llm_config_error_circuit_reset_seconds,
        clock=clock,
    )


_loop_llm_config_circuits: WeakKeyDictionary[
    asyncio.AbstractEventLoop,
    dict[tuple[str, str], LlmConfigurationErrorCircuit],
] = WeakKeyDictionary()


def _get_loop_llm_config_circuit(
    settings: Settings,
    *,
    project_id: str,
    model_name: str,
) -> LlmConfigurationErrorCircuit:
    """Share one circuit across the steps of a run, as the limiters are shared.

    Every step builds its own client, so a per-client circuit would forget what
    the previous step just learned and re-discover the same rejection from
    scratch. Scoping it to the loop and the project/model keeps one verdict for
    the deployment the calls actually target.
    """
    loop = asyncio.get_running_loop()
    scoped_circuits = _loop_llm_config_circuits.setdefault(loop, {})
    scope = (project_id, model_name)
    circuit = scoped_circuits.get(scope)
    if circuit is None:
        circuit = build_llm_configuration_error_circuit(settings)
        scoped_circuits[scope] = circuit
    return circuit


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
        circuit: LlmConfigurationErrorCircuit | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter_random: Callable[[], float] = random.random,
    ) -> None:
        self._settings = settings or get_settings()
        self._rate_limiter = rate_limiter
        self._token_limiter = token_limiter
        self._circuit = circuit
        self._sleeper = sleeper
        self._jitter_random = jitter_random

    def is_configured(self) -> bool:
        return bool(self._settings.gemini_api_key)

    @property
    def model_name(self) -> str:
        return self._settings.llm_model

    @property
    def concurrency_limit(self) -> int:
        return self._settings.llm_concurrency_limit

    @property
    def input_token_budget(self) -> int:
        """Largest estimated input a single request may reserve.

        A request estimated above this is rejected outright by the token
        limiter rather than queued, so callers that build a variable-length
        payload must fit it to this budget before invoking.
        """
        return self._settings.llm_tokens_per_minute

    def _call_retry_delay(self, attempt: int, retry_after: float | None) -> float:
        """Back off before reissuing a call, honouring a provider-sent delay."""
        if retry_after is not None:
            return max(0.0, retry_after)
        base_delay = self._settings.llm_call_retry_base_delay_seconds * (
            2 ** max(attempt - 1, 0)
        )
        capped_delay = min(base_delay, self._settings.llm_call_retry_max_delay_seconds)
        jitter = (
            capped_delay * self._settings.llm_retry_jitter_ratio * self._jitter_random()
        )
        return max(0.0, capped_delay + jitter)

    def _build_model(
        self, *, response_schema: dict[str, Any] | None = None
    ) -> ChatGoogleGenerativeAI:
        if not self.is_configured():
            raise LlmConfigurationError('Gemini API key is not configured.')
        # response_schema reaches the request only alongside this mime type;
        # the client raises otherwise.
        response_mime_type = 'application/json' if response_schema else None
        return ChatGoogleGenerativeAI(
            model=self._settings.llm_model,
            google_api_key=self._settings.gemini_api_key,
            temperature=self._settings.llm_temperature,
            max_retries=0,
            response_mime_type=response_mime_type,
            response_schema=response_schema,
        )

    async def invoke_json(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        response_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call the model and parse one JSON object from its reply.

        ``response_schema`` constrains the reply at the provider instead of
        in prose. A prompt can only ask; a schema is enforced, which is the
        difference that matters for fields whose allowed values the model
        would otherwise pick freely.
        """
        circuit = self._circuit
        if circuit is None:
            circuit = _get_loop_llm_config_circuit(
                self._settings,
                project_id=self._settings.llm_quota_project_id,
                model_name=self._settings.llm_model,
            )
        # Checked before the limiters so an open circuit costs nothing: the
        # per-minute queue is exactly where a doomed run loses its time.
        if not circuit.allow_request():
            raise LlmConfigurationError(
                'LLM provider is rejecting requests for a configuration reason.'
            )

        model = self._build_model(response_schema=response_schema)
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
        # The reservation covers the whole call: in-call retries reissue the same
        # request within seconds, so re-reserving would double-spend the window.
        await rate_limiter.acquire()
        reservation = await token_limiter.acquire(estimated_tokens)
        try:
            response = await self._invoke_with_call_retries(
                model,
                messages,
                rate_limiter=rate_limiter,
            )
        except Exception as exc:
            if _is_configuration_provider_error(exc) and circuit.record_rejection():
                log_safe_exception(
                    LOGGER,
                    logging.WARNING,
                    'Suspending LLM calls after repeated configuration rejections.',
                    exception=exc,
                    context={'model': self._settings.llm_model},
                )
            raise
        circuit.record_success()
        actual_tokens = _actual_input_tokens(response)
        if actual_tokens is not None:
            await token_limiter.reconcile(reservation, actual_tokens)
        return self._parse_json(self._extract_text_content(response.content))

    async def _invoke_with_call_retries(
        self,
        model: Any,
        messages: list[tuple[str, str]],
        *,
        rate_limiter: _AsyncRateLimiter,
    ) -> Any:
        """Issue one request, reissuing it while the provider is merely blipping.

        A 429 is a quota verdict, so reissuing it now would only deepen the
        overage; it escalates to the durable worker's backoff instead. A 5xx or
        transport failure usually clears within seconds, so it is reissued here
        rather than costing the whole job a restart.
        """
        max_attempts = max(1, self._settings.llm_call_max_attempts)
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                await rate_limiter.acquire()
            try:
                return await asyncio.wait_for(
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
                retry_after = _retry_after_seconds(exc)
                if attempt >= max_attempts or not _is_transient_provider_error(exc):
                    raise LlmRetryableError(retry_after_seconds=retry_after) from exc
                log_safe_exception(
                    LOGGER,
                    logging.WARNING,
                    'Retrying a transient LLM provider failure in place.',
                    exception=exc,
                )
                await self._sleeper(self._call_retry_delay(attempt, retry_after))
        raise LlmRetryableError()

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
    'LlmConfigurationErrorCircuit',
    'LlmRetryExhaustedError',
    'LlmRetryableError',
    'LlmTimeoutError',
    'TokenReservation',
    'build_llm_configuration_error_circuit',
    'estimate_input_tokens',
    'llm_retry_exhausted_mode',
]
