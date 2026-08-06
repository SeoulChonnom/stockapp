from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.response import ApiError, ApiErrorDetail, ApiSuccess, MetaPayload
from app.core.timezone import isoformat_datetime

Meta = MetaPayload
SuccessEnvelope = ApiSuccess


def normalize_timestamp(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return isoformat_datetime(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return value
        return isoformat_datetime(parsed)
    return value


__all__ = [
    'ApiError',
    'ApiErrorDetail',
    'ApiSuccess',
    'Meta',
    'MetaPayload',
    'SuccessEnvelope',
    'normalize_timestamp',
]
