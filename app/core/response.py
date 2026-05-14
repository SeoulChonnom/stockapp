from __future__ import annotations

from datetime import UTC, datetime
from typing import TypeVar

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


class ApiError(BaseModel):
    success: bool = False
    error: ApiErrorDetail
    meta: MetaPayload = Field(default_factory=lambda: build_meta())


def build_meta() -> MetaPayload:
    return MetaPayload(
        requestId=get_request_id(),
        timestamp=datetime.now(tz=UTC),
    )


__all__ = ['ApiError', 'ApiErrorDetail', 'ApiSuccess', 'MetaPayload', 'build_meta']
