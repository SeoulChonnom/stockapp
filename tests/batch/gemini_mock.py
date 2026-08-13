from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import httpx

from app.core.llm import LlmRetryExhaustedError


def gemini_json_response(payload: object) -> dict[str, Any]:
    """Build a complete successful Gemini generateContent response."""
    return {
        'candidates': [
            {
                'content': {
                    'parts': [{'text': json.dumps(payload, ensure_ascii=False)}],
                    'role': 'model',
                },
                'finishReason': 'STOP',
                'index': 0,
                'safetyRatings': [
                    {
                        'category': 'HARM_CATEGORY_DANGEROUS_CONTENT',
                        'probability': 'NEGLIGIBLE',
                    }
                ],
            }
        ],
        'promptFeedback': {'safetyRatings': []},
        'usageMetadata': {
            'promptTokenCount': 42,
            'candidatesTokenCount': 31,
            'totalTokenCount': 73,
            'promptTokensDetails': [{'modality': 'TEXT', 'tokenCount': 42}],
            'candidatesTokensDetails': [{'modality': 'TEXT', 'tokenCount': 31}],
        },
        'modelVersion': 'gemini-2.5-flash',
        'responseId': 'mock-response-id',
    }


def gemini_exhausted_response() -> dict[str, Any]:
    """Build Gemini's documented RESOURCE_EXHAUSTED error shape."""
    return {
        'error': {
            'code': 429,
            'message': 'Quota exceeded for secret-project-token.',
            'status': 'RESOURCE_EXHAUSTED',
            'details': [
                {
                    '@type': 'type.googleapis.com/google.rpc.RetryInfo',
                    'retryDelay': '30s',
                },
                {
                    '@type': 'type.googleapis.com/google.rpc.QuotaFailure',
                    'violations': [
                        {
                            'quotaMetric': (
                                'generativelanguage.googleapis.com/'
                                'generate_content_free_tier_requests'
                            ),
                            'quotaId': 'GenerateRequestsPerMinutePerProject',
                        }
                    ],
                },
            ],
        }
    }


class MockGeminiApiClient:
    """Gemini client double backed by an in-memory HTTP API transport."""

    def __init__(
        self,
        responses: Sequence[tuple[int, dict[str, Any]] | BaseException],
    ) -> None:
        self._responses = list(responses)
        self.request_payloads: list[dict[str, Any]] = []

    def is_configured(self) -> bool:
        return True

    @property
    def model_name(self) -> str:
        return 'gemini-2.5-flash'

    @property
    def concurrency_limit(self) -> int:
        return 1

    async def invoke_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        request_payload = {
            'systemInstruction': {'parts': [{'text': system_prompt}]},
            'contents': [
                {
                    'role': 'user',
                    'parts': [{'text': user_prompt}],
                }
            ],
            'generationConfig': {'responseMimeType': 'application/json'},
        }

        async def handle(request: httpx.Request) -> httpx.Response:
            self.request_payloads.append(json.loads(request.content))
            response = self._responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            status_code, payload = response
            return httpx.Response(status_code, json=payload, request=request)

        transport = httpx.MockTransport(handle)
        async with httpx.AsyncClient(
            transport=transport,
            base_url='https://generativelanguage.googleapis.com',
        ) as client:
            response = await client.post(
                '/v1beta/models/gemini-2.5-flash:generateContent',
                params={'key': 'mock-api-key'},
                json=request_payload,
            )

        payload = response.json()
        if response.status_code == 429:
            raise LlmRetryExhaustedError(payload['error']['message'])
        response.raise_for_status()
        text = payload['candidates'][0]['content']['parts'][0]['text']
        decoded = json.loads(text)
        if not isinstance(decoded, dict):
            raise TypeError('Gemini JSON response must decode to an object.')
        return decoded


__all__ = [
    'MockGeminiApiClient',
    'gemini_exhausted_response',
    'gemini_json_response',
]
