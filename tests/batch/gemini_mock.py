from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage

from app.core.llm import (
    GeminiJsonClient,
    TokenReservation,
    build_llm_configuration_error_circuit,
)
from app.core.settings import Settings


class MockGeminiModel:
    """Deterministic model double injected below GeminiJsonClient's SDK boundary."""

    def __init__(self, responses: Sequence[AIMessage | BaseException]) -> None:
        self._responses = list(responses)
        self.messages: list[list[tuple[str, str]]] = []

    @property
    def call_count(self) -> int:
        return len(self.messages)

    async def ainvoke(self, messages: list[tuple[str, str]]) -> AIMessage:
        self.messages.append(messages)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class RecordingRateLimiter:
    def __init__(self) -> None:
        self.acquire_count = 0

    async def acquire(self) -> None:
        self.acquire_count += 1


class RecordingTokenLimiter:
    def __init__(self) -> None:
        self.estimates: list[int] = []
        self.reconciliations: list[tuple[int, int]] = []

    async def acquire(self, token_count: int) -> TokenReservation:
        self.estimates.append(token_count)
        return TokenReservation(0.0, token_count)

    async def reconcile(
        self,
        reservation: TokenReservation,
        actual_token_count: int,
    ) -> None:
        self.reconciliations.append((reservation.token_count, actual_token_count))


@dataclass(frozen=True, slots=True)
class MockGeminiHarness:
    client: GeminiJsonClient
    model: MockGeminiModel
    rate_limiter: RecordingRateLimiter
    token_limiter: RecordingTokenLimiter
    slept: list[float]
    clock: FakeClock
    # Every response_schema the client built a model with, so a test can
    # assert the schema actually reached the provider call rather than
    # trusting that passing it through was enough.
    response_schemas: list[dict[str, Any] | None]


class FakeClock:
    """A monotonic clock a test advances by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def build_mock_gemini_harness(
    monkeypatch: Any,
    responses: Sequence[AIMessage | BaseException],
    *,
    settings: Settings | None = None,
) -> MockGeminiHarness:
    """Inject a deterministic model into the production Gemini JSON client."""
    model = MockGeminiModel(responses)
    rate_limiter = RecordingRateLimiter()
    token_limiter = RecordingTokenLimiter()
    clock = FakeClock()
    slept: list[float] = []

    async def record_sleep(seconds: float) -> None:
        slept.append(seconds)

    resolved_settings = settings or Settings(
        app_env='development',
        gemini_api_key='mock-api-key',
        llm_model='gemini-2.5-flash',
        llm_timeout_seconds=1,
    )
    # The circuit is supplied rather than resolved from the running loop so each
    # harness starts closed; the loop-scoped registry is deliberately shared.
    client = GeminiJsonClient(
        resolved_settings,
        rate_limiter=rate_limiter,
        token_limiter=token_limiter,
        circuit=build_llm_configuration_error_circuit(resolved_settings, clock=clock),
        sleeper=record_sleep,
        jitter_random=lambda: 0.0,
    )
    response_schemas: list[dict[str, Any] | None] = []

    def build_model(
        *, response_schema: dict[str, Any] | None = None
    ) -> MockGeminiModel:
        response_schemas.append(response_schema)
        return model

    monkeypatch.setattr(client, '_build_model', build_model)
    return MockGeminiHarness(
        client=client,
        model=model,
        rate_limiter=rate_limiter,
        token_limiter=token_limiter,
        slept=slept,
        clock=clock,
        response_schemas=response_schemas,
    )


def gemini_ai_message(
    payload: object,
    *,
    split_text_blocks: bool = False,
) -> AIMessage:
    """Build an SDK-level Gemini response with real AIMessage content."""
    content = json.dumps(payload, ensure_ascii=False)
    if split_text_blocks:
        midpoint = len(content) // 2
        response_content: str | list[dict[str, str]] = [
            {'type': 'text', 'text': content[:midpoint]},
            {'type': 'text', 'text': content[midpoint:]},
        ]
    else:
        response_content = content
    return AIMessage(
        content=response_content,
        usage_metadata={
            'input_tokens': 23,
            'output_tokens': 17,
            'total_tokens': 40,
        },
        response_metadata={
            'model_name': 'gemini-2.5-flash',
            'finish_reason': 'STOP',
            'safety_ratings': [],
        },
    )


def malformed_gemini_ai_message(content: str = '{"keyPoints":') -> AIMessage:
    """Build an SDK response whose text cannot be decoded as JSON."""
    return AIMessage(
        content=[{'type': 'text', 'text': content}],
        response_metadata={
            'model_name': 'gemini-2.5-flash',
            'finish_reason': 'STOP',
            'safety_ratings': [],
        },
    )


__all__ = [
    'FakeClock',
    'MockGeminiHarness',
    'build_mock_gemini_harness',
    'gemini_ai_message',
    'malformed_gemini_ai_message',
]
