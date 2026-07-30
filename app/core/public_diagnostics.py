from __future__ import annotations

import re

AI_PROVIDER_FAILURE_CODE = 'AI_PROVIDER_REQUEST_FAILED'
AI_PROVIDER_FAILURE_MESSAGE = 'AI provider request failed; fallback content was used.'
AI_PROVIDER_INVALID_RESPONSE_CODE = 'AI_PROVIDER_RESPONSE_INVALID'
AI_PROVIDER_INVALID_RESPONSE_MESSAGE = (
    'AI provider returned an invalid response; fallback content was used.'
)
EXTERNAL_PROVIDER_FAILURE_CODE = 'EXTERNAL_PROVIDER_REQUEST_FAILED'
EXTERNAL_PROVIDER_FAILURE_MESSAGE = 'External provider request failed.'

_SENSITIVE_PROVIDER_MARKERS = (
    '429',
    'api key',
    'api_key',
    'authorization',
    'bearer ',
    'endpoint',
    'gemini',
    'generativelanguage',
    'googleapis.com',
    'grpc_status',
    'quota',
    'resource_exhausted',
    'response body',
    'retryinfo',
    'retry info',
    'secret-token',
    'secret_token',
)
_SAFE_SCOPE_PREFIX = re.compile(
    r'^(?P<prefix>(?:Cluster enrichment|AI summary) fallback for [^:]+):',
    flags=re.IGNORECASE,
)


def public_ai_provider_error(exc: BaseException) -> dict[str, str]:
    """Build provider failure metadata safe for persistence and public APIs."""
    return {
        'code': AI_PROVIDER_FAILURE_CODE,
        'message': AI_PROVIDER_FAILURE_MESSAGE,
        'errorClass': type(exc).__name__,
    }


def public_ai_invalid_response() -> dict[str, str]:
    """Build stable metadata for malformed AI responses."""
    return {
        'code': AI_PROVIDER_INVALID_RESPONSE_CODE,
        'message': AI_PROVIDER_INVALID_RESPONSE_MESSAGE,
        'errorClass': 'ValueError',
    }


def public_external_provider_error(error_class: str) -> dict[str, str]:
    """Build provider-neutral failure metadata safe for batch events."""
    return {
        'code': EXTERNAL_PROVIDER_FAILURE_CODE,
        'message': EXTERNAL_PROVIDER_FAILURE_MESSAGE,
        'errorClass': error_class,
    }


def sanitize_public_diagnostic(value: str | None) -> str | None:
    """Redact legacy provider payloads while preserving ordinary diagnostics."""
    if value is None:
        return None
    sanitized: list[str] = []
    for fragment in value.split('; '):
        replacement = _sanitize_fragment(fragment)
        if replacement and replacement not in sanitized:
            sanitized.append(replacement)
    return '; '.join(sanitized) if sanitized else None


def sanitize_public_diagnostics(values: list[str]) -> list[str]:
    """Sanitize and de-duplicate a list of user-visible diagnostics."""
    sanitized: list[str] = []
    for value in values:
        safe_value = sanitize_public_diagnostic(value)
        if safe_value and safe_value not in sanitized:
            sanitized.append(safe_value)
    return sanitized


def _sanitize_fragment(fragment: str) -> str:
    match = _SAFE_SCOPE_PREFIX.match(fragment)
    if match:
        return f'{match.group("prefix")}: {AI_PROVIDER_FAILURE_MESSAGE}'
    normalized = fragment.casefold()
    if not any(marker in normalized for marker in _SENSITIVE_PROVIDER_MARKERS):
        return fragment
    return AI_PROVIDER_FAILURE_MESSAGE


__all__ = [
    'AI_PROVIDER_FAILURE_CODE',
    'AI_PROVIDER_FAILURE_MESSAGE',
    'AI_PROVIDER_INVALID_RESPONSE_CODE',
    'AI_PROVIDER_INVALID_RESPONSE_MESSAGE',
    'EXTERNAL_PROVIDER_FAILURE_CODE',
    'EXTERNAL_PROVIDER_FAILURE_MESSAGE',
    'public_ai_invalid_response',
    'public_ai_provider_error',
    'public_external_provider_error',
    'sanitize_public_diagnostic',
    'sanitize_public_diagnostics',
]
