from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from urllib.parse import urlparse

from app.core.public_diagnostics import sanitize_public_diagnostic
from app.core.settings import get_settings

MAX_ERROR_LOG_CHARS = 32 * 1024
REDACTED = '[REDACTED]'
_TRUNCATION_MARKER = '\n...[TRUNCATED]...\n'
_AUTHORIZATION_PATTERN = re.compile(
    r'(authorization\s*:\s*(?:bearer|basic)\s+)[^\s,;]+',
    flags=re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r'('
    r'(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|'
    r'jwt(?:[_-]?secret)?|password|passwd|secret|token)'
    r'["\']?\s*(?:=|:)\s*["\']?'
    r')[^"\'\s,}&]+',
    flags=re.IGNORECASE,
)
_URL_USERINFO_PATTERN = re.compile(
    r'([a-z][a-z0-9+.-]*://)[^/\s@]+@',
    flags=re.IGNORECASE,
)
_JWT_PATTERN = re.compile(
    r'\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b',
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class StepErrorDiagnostics:
    """Safe diagnostic text for a failed batch step."""

    error_message: str
    error_log: str


def build_step_error_diagnostics(
    exception: BaseException,
    *,
    public_message: str,
) -> StepErrorDiagnostics:
    """Build a public-safe message and masked traceback for an exception."""
    safe_message = sanitize_public_diagnostic(public_message) or 'Batch step failed.'
    raw_traceback = ''.join(traceback.format_exception(exception, chain=True))
    return StepErrorDiagnostics(safe_message, mask_error_log(raw_traceback) or '')


def mask_error_log(value: str | None) -> str | None:
    """Redact configured and common credentials from diagnostic text."""
    if value is None:
        return None

    masked = value
    for sensitive_value in _sensitive_values():
        masked = re.compile(re.escape(sensitive_value), flags=re.IGNORECASE).sub(
            REDACTED,
            masked,
        )
    masked = _AUTHORIZATION_PATTERN.sub(rf'\1{REDACTED}', masked)
    masked = _CREDENTIAL_VALUE_PATTERN.sub(rf'\1{REDACTED}', masked)
    masked = _URL_USERINFO_PATTERN.sub(rf'\1{REDACTED}@', masked)
    masked = _JWT_PATTERN.sub(REDACTED, masked)
    return _truncate(masked)


def _sensitive_values() -> tuple[str, ...]:
    settings = get_settings()
    database = urlparse(settings.database_url)
    values = (
        database.username,
        database.password,
        settings.jwt_secret,
        settings.auth_stub_token,
        settings.naver_client_secret,
        settings.gemini_api_key,
    )
    return tuple(
        value for value in values if isinstance(value, str) and len(value) >= 4
    )


def _truncate(value: str) -> str:
    if len(value) <= MAX_ERROR_LOG_CHARS:
        return value

    remaining = MAX_ERROR_LOG_CHARS - len(_TRUNCATION_MARKER)
    beginning_length = remaining // 2
    ending_length = remaining - beginning_length
    return value[:beginning_length] + _TRUNCATION_MARKER + value[-ending_length:]


__all__ = [
    'MAX_ERROR_LOG_CHARS',
    'REDACTED',
    'StepErrorDiagnostics',
    'build_step_error_diagnostics',
    'mask_error_log',
]
