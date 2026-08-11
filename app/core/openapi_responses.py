"""Shared OpenAPI response documentation for the ``ApiError`` envelope.

These constants are pure schema metadata: they tell FastAPI which status
codes and error codes a route can realistically return so that
``openapi.json`` documents them under ``responses``. They do not change any
runtime behavior — the actual status codes and bodies are produced by the
handlers registered in :mod:`app.core.exceptions`.
"""

from __future__ import annotations

from typing import Any

from app.core.response import ApiError

type ResponsesSpec = dict[int | str, dict[str, Any]]


def _error(description: str) -> dict[str, Any]:
    return {'model': ApiError, 'description': description}


def error_response(status_code: int, description: str) -> ResponsesSpec:
    """Build a single-status ``ApiError`` response entry for route docs."""
    return {status_code: _error(description)}


def merge_responses(*specs: ResponsesSpec) -> ResponsesSpec:
    """Merge response specs; later specs win on key collisions."""
    merged: ResponsesSpec = {}
    for spec in specs:
        merged.update(spec)
    return merged


SERVER_ERROR_RESPONSE: ResponsesSpec = error_response(
    500,
    'Unexpected server error. Codes: INTERNAL_SERVER_ERROR.',
)

UNAUTHORIZED_RESPONSE: ResponsesSpec = error_response(
    401,
    'Missing, invalid, or expired bearer token. '
    'Codes: AUTH_MISSING_BEARER_TOKEN, AUTH_TOKEN_EXPIRED, AUTH_INVALID_TOKEN.',
)

FORBIDDEN_RESPONSE: ResponsesSpec = error_response(
    403,
    'Authenticated user lacks the required role. Codes: AUTH_FORBIDDEN.',
)

# Applies to every route: the generic exception handler can fire anywhere.
BASE_RESPONSES: ResponsesSpec = merge_responses(SERVER_ERROR_RESPONSE)

# Applies to every authenticated route (everything except /health).
AUTH_RESPONSES: ResponsesSpec = merge_responses(
    BASE_RESPONSES,
    UNAUTHORIZED_RESPONSE,
    FORBIDDEN_RESPONSE,
)


__all__ = [
    'AUTH_RESPONSES',
    'BASE_RESPONSES',
    'FORBIDDEN_RESPONSE',
    'SERVER_ERROR_RESPONSE',
    'UNAUTHORIZED_RESPONSE',
    'error_response',
    'merge_responses',
]
