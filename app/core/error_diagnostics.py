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
_CREDENTIAL_KEY_PATTERN = (
    r'(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|'
    r'credential(?:s)?|jwt(?:[_-]?secret)?|password|passwd|secret|token)'
)
_QUOTED_VALUE_CONTENT_PATTERN = r'(?:\\[\s\S]|(?!(?:\\|(?P=value_quote)))[\s\S])*'
_QUOTED_AUTHORIZATION_PATTERN = re.compile(
    r'(?P<prefix>(?P<key_quote>["\']?)authorization(?P=key_quote)\s*'
    r'(?:=|:)\s*(?P<value_quote>["\'])(?:bearer|basic)\s+)'
    rf'{_QUOTED_VALUE_CONTENT_PATTERN}(?P=value_quote)',
    flags=re.IGNORECASE,
)
_AUTHORIZATION_PATTERN = re.compile(
    r'(authorization\s*(?:=|:)\s*(?:bearer|basic)\s+)[^\r\n,;]+',
    flags=re.IGNORECASE,
)
_QUOTED_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r'(?P<prefix>(?P<key_quote>["\']?)authorization(?P=key_quote)\s*'
    r'(?:=|:)\s*(?P<value_quote>["\']))'
    r'(?!(?:bearer|basic)\s+)'
    rf'{_QUOTED_VALUE_CONTENT_PATTERN}(?P=value_quote)',
    flags=re.IGNORECASE,
)
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r'(authorization\s*(?:=|:)\s*)(?!(?:bearer|basic)\s+)[^\s,;}&]+',
    flags=re.IGNORECASE,
)
_QUOTED_CREDENTIAL_VALUE_PATTERN = re.compile(
    rf'(?P<prefix>(?P<key_quote>["\']?){_CREDENTIAL_KEY_PATTERN}'
    r'(?P=key_quote)\s*(?:=|:)\s*(?P<value_quote>["\']))'
    rf'{_QUOTED_VALUE_CONTENT_PATTERN}(?P=value_quote)',
    flags=re.IGNORECASE,
)
_CREDENTIAL_VALUE_PATTERN = re.compile(
    rf'({_CREDENTIAL_KEY_PATTERN}["\']?\s*(?:=|:)\s*["\']?)'
    r'[^"\'\s,}&]+',
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
    raw_final_exception = ''.join(traceback.format_exception_only(exception))
    masked_traceback = _mask(raw_traceback)
    masked_final_exception = _mask(raw_final_exception)
    return StepErrorDiagnostics(
        safe_message,
        _truncate(masked_traceback, final_exception=masked_final_exception),
    )


def mask_error_log(value: str | None) -> str | None:
    """Redact configured and common credentials from diagnostic text."""
    if value is None:
        return None

    return _truncate(_mask(value))


def _mask(value: str) -> str:
    masked = value
    for sensitive_value in _sensitive_values():
        masked = re.compile(re.escape(sensitive_value), flags=re.IGNORECASE).sub(
            REDACTED,
            masked,
        )
    masked = _QUOTED_AUTHORIZATION_PATTERN.sub(
        rf'\g<prefix>{REDACTED}\g<value_quote>',
        masked,
    )
    masked = _AUTHORIZATION_PATTERN.sub(rf'\1{REDACTED}', masked)
    masked = _QUOTED_AUTHORIZATION_VALUE_PATTERN.sub(
        rf'\g<prefix>{REDACTED}\g<value_quote>',
        masked,
    )
    masked = _AUTHORIZATION_VALUE_PATTERN.sub(rf'\1{REDACTED}', masked)
    masked = _QUOTED_CREDENTIAL_VALUE_PATTERN.sub(
        rf'\g<prefix>{REDACTED}\g<value_quote>',
        masked,
    )
    masked = _CREDENTIAL_VALUE_PATTERN.sub(rf'\1{REDACTED}', masked)
    masked = _URL_USERINFO_PATTERN.sub(rf'\1{REDACTED}@', masked)
    masked = _JWT_PATTERN.sub(REDACTED, masked)
    return masked


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


def _truncate(value: str, *, final_exception: str | None = None) -> str:
    if len(value) <= MAX_ERROR_LOG_CHARS:
        return value

    if final_exception:
        remaining = MAX_ERROR_LOG_CHARS - len(_TRUNCATION_MARKER)
        if len(final_exception) <= remaining:
            beginning_length = remaining - len(final_exception)
            return value[:beginning_length] + _TRUNCATION_MARKER + final_exception

        segmented_remaining = MAX_ERROR_LOG_CHARS - (2 * len(_TRUNCATION_MARKER))
        beginning_length = segmented_remaining // 3
        final_prefix_length = segmented_remaining // 3
        ending_length = segmented_remaining - beginning_length - final_prefix_length
        return (
            value[:beginning_length]
            + _TRUNCATION_MARKER
            + final_exception[:final_prefix_length]
            + _TRUNCATION_MARKER
            + final_exception[-ending_length:]
        )

    remaining = MAX_ERROR_LOG_CHARS - len(_TRUNCATION_MARKER)
    beginning_length = remaining // 2
    ending_length = remaining - beginning_length
    final_line_start = value.rfind('\n', 0, len(value.rstrip('\n'))) + 1
    final_line = value[final_line_start:]
    if len(final_line) <= ending_length:
        return value[:beginning_length] + _TRUNCATION_MARKER + value[-ending_length:]

    segmented_remaining = MAX_ERROR_LOG_CHARS - (2 * len(_TRUNCATION_MARKER))
    beginning_length = segmented_remaining // 2
    final_prefix_length = segmented_remaining // 4
    ending_length = segmented_remaining - beginning_length - final_prefix_length
    return (
        value[:beginning_length]
        + _TRUNCATION_MARKER
        + final_line[:final_prefix_length]
        + _TRUNCATION_MARKER
        + value[-ending_length:]
    )


__all__ = [
    'MAX_ERROR_LOG_CHARS',
    'REDACTED',
    'StepErrorDiagnostics',
    'build_step_error_diagnostics',
    'mask_error_log',
]
