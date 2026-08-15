from __future__ import annotations

import logging
import weakref
from datetime import date
from traceback import extract_tb

_MANAGED_UVICORN_HANDLERS: weakref.WeakSet[logging.Handler] = weakref.WeakSet()


def configure_batch_logging() -> None:
    """Route ``app.batch`` INFO records through Uvicorn's existing console handler."""
    batch_logger = logging.getLogger('app.batch')
    batch_logger.setLevel(logging.INFO)
    batch_logger.disabled = False

    console_handlers: tuple[logging.Handler, ...] = ()
    for logger_name in ('uvicorn.error', 'uvicorn'):
        handlers = tuple(logging.getLogger(logger_name).handlers)
        if handlers:
            console_handlers = handlers
            break

    for handler in tuple(batch_logger.handlers):
        if handler in _MANAGED_UVICORN_HANDLERS and handler not in console_handlers:
            batch_logger.removeHandler(handler)

    for handler in console_handlers:
        if handler not in batch_logger.handlers:
            batch_logger.addHandler(handler)
        _MANAGED_UVICORN_HANDLERS.add(handler)

    if console_handlers:
        batch_logger.propagate = False
    elif not batch_logger.handlers:
        batch_logger.propagate = True


def log_safe_exception(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exception: BaseException,
) -> None:
    """Log an exception class and traceback frames without its message or payload."""
    exception_class = type(exception).__name__
    traceback_frames = _safe_traceback_frames(exception)
    cause_chain = _safe_cause_chain(exception)
    logger.log(
        level,
        '%s exception_class=%s caused_by=%s traceback=%s',
        message,
        exception_class,
        cause_chain,
        traceback_frames,
        extra={
            'batch_exception_class': exception_class,
            'batch_caused_by': cause_chain,
            'batch_traceback': traceback_frames,
        },
    )


def log_batch_lifecycle(
    logger: logging.Logger,
    level: int,
    *,
    event: str,
    job_id: int,
    page_id: int | None,
    reference_date: date | None,
    stage: str,
    duration_seconds: float | None = None,
    exception: BaseException | None = None,
) -> None:
    """Emit a safe, structured batch lifecycle event.

    Exception messages and source lines are deliberately omitted because
    upstream provider errors can contain response bodies or credentials. File,
    line, and function frames are retained so the failure remains traceable.
    """
    exception_class = type(exception).__name__ if exception is not None else None
    traceback_frames = (
        _safe_traceback_frames(exception) if exception is not None else None
    )
    cause_chain = _safe_cause_chain(exception) if exception is not None else None
    duration_value = (
        round(duration_seconds, 3) if duration_seconds is not None else None
    )
    logger.log(
        level,
        (
            'batch_lifecycle event=%s job_id=%s page_id=%s reference_date=%s '
            'stage=%s duration_seconds=%s exception_class=%s caused_by=%s '
            'traceback=%s'
        ),
        event,
        job_id,
        page_id,
        reference_date,
        stage,
        duration_value,
        exception_class,
        cause_chain,
        traceback_frames,
        extra={
            'batch_event': event,
            'batch_job_id': job_id,
            'batch_page_id': page_id,
            'batch_reference_date': reference_date,
            'batch_stage': stage,
            'batch_duration_seconds': duration_value,
            'batch_exception_class': exception_class,
            'batch_caused_by': cause_chain,
            'batch_traceback': traceback_frames,
        },
    )


def _safe_cause_chain(exception: BaseException) -> str | None:
    """Name the wrapped causes, with any HTTP status, but never their messages.

    Sanitized wrappers such as ``LlmRetryableError`` hide which provider
    failure actually occurred, and a 429 and a 503 call for opposite responses.
    Class names and status codes carry no response body or credentials, so they
    are safe to record where the message is not.
    """
    causes: list[str] = []
    seen: list[int] = []
    current = exception.__cause__ or exception.__context__
    while current is not None and id(current) not in seen and len(causes) < 5:
        seen.append(id(current))
        status_code = _safe_status_code(current)
        name = type(current).__name__
        causes.append(f'{name}({status_code})' if status_code is not None else name)
        current = current.__cause__ or current.__context__
    return ' <- '.join(causes) or None


def _safe_status_code(exception: BaseException) -> int | None:
    for value in (
        getattr(exception, 'code', None),
        getattr(exception, 'status_code', None),
        getattr(getattr(exception, 'response', None), 'status_code', None),
    ):
        if isinstance(value, int):
            return value
    return None


def _safe_traceback_frames(exception: BaseException) -> str:
    frames = extract_tb(exception.__traceback__)
    return ' <- '.join(
        f'{frame.filename}:{frame.lineno}:{frame.name}' for frame in frames
    )


__all__ = [
    'configure_batch_logging',
    'log_batch_lifecycle',
    'log_safe_exception',
]
