from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, Field

from app.core.request_context import get_request_id

T = TypeVar('T')


class MetaPayload(BaseModel):
    requestId: str
    timestamp: datetime


class ApiSuccess[T](BaseModel):
    success: bool = True
    data: T
    meta: MetaPayload = Field(default_factory=lambda: build_meta())


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    """Optional machine-readable context for this error code.

    Omitted from the response body entirely when unset -- see
    :func:`build_error_body`. Keys are camelCase to match the rest of the
    API surface (e.g. ``{'jobId': 42}`` for ``BATCH_ALREADY_RUNNING``).
    """


class ApiError(BaseModel):
    success: bool = False
    error: ApiErrorDetail
    meta: MetaPayload = Field(default_factory=lambda: build_meta())


def build_meta() -> MetaPayload:
    return MetaPayload(
        requestId=get_request_id(),
        timestamp=datetime.now(tz=UTC),
    )


def build_error_body(detail: ApiErrorDetail) -> dict[str, Any]:
    """Serialize an error envelope for a ``JSONResponse``.

    ``exclude_none`` keeps optional fields such as ``error.details`` out of
    the JSON body instead of emitting ``"details": null``, so responses that
    carry no structured context stay byte-identical to what they were before
    the field existed. Every error path must go through this helper rather
    than calling ``model_dump`` directly, or that guarantee silently breaks.
    No required field on the envelope is ever ``None``, so nothing else is
    affected by the exclusion.
    """
    return ApiError(error=detail).model_dump(mode='json', exclude_none=True)


__all__ = [
    'ApiError',
    'ApiErrorDetail',
    'ApiSuccess',
    'MetaPayload',
    'build_error_body',
    'build_meta',
]
