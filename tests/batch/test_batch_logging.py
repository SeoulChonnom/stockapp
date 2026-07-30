from __future__ import annotations

import logging
from datetime import date
from io import StringIO

from app.batch.logging import (
    configure_batch_logging,
    log_batch_lifecycle,
    log_safe_exception,
)


def test_configure_batch_logging_reuses_uvicorn_handler_without_duplicates(
    monkeypatch,
) -> None:
    output = StringIO()
    console_handler = logging.StreamHandler(output)
    batch_logger = logging.getLogger('app.batch')
    uvicorn_logger = logging.getLogger('uvicorn')
    uvicorn_error_logger = logging.getLogger('uvicorn.error')
    access_logger = logging.getLogger('uvicorn.access')
    access_handlers = tuple(access_logger.handlers)
    monkeypatch.setattr(batch_logger, 'handlers', [])
    monkeypatch.setattr(batch_logger, 'propagate', True)
    monkeypatch.setattr(uvicorn_logger, 'handlers', [console_handler])
    monkeypatch.setattr(uvicorn_error_logger, 'handlers', [])

    configure_batch_logging()
    configure_batch_logging()
    logging.getLogger('app.batch.worker').info('visible batch lifecycle')

    assert batch_logger.handlers == [console_handler]
    assert output.getvalue().count('visible batch lifecycle') == 1
    assert tuple(access_logger.handlers) == access_handlers


def test_log_safe_exception_omits_exception_message_and_dsn(caplog) -> None:
    logger = logging.getLogger('test.batch.safe_exception')
    caplog.set_level(logging.ERROR, logger=logger.name)

    try:
        raise RuntimeError('postgresql://admin:secret@db.example.com/stock')
    except RuntimeError as exc:
        log_safe_exception(
            logger,
            logging.ERROR,
            'Background queue operation failed.',
            exception=exc,
        )

    record = caplog.records[-1]
    assert record.batch_exception_class == 'RuntimeError'
    assert record.exc_info is None
    assert 'test_batch_logging.py' in record.batch_traceback
    assert 'postgresql://' not in caplog.text
    assert 'secret' not in caplog.text


def test_log_batch_lifecycle_exposes_safe_structured_failure(
    caplog,
) -> None:
    logger = logging.getLogger('test.batch.lifecycle')
    caplog.set_level(logging.ERROR, logger=logger.name)

    try:
        raise TimeoutError('provider response contained secret-token')
    except TimeoutError as exc:
        log_batch_lifecycle(
            logger,
            logging.ERROR,
            event='failed',
            job_id=1001,
            page_id=501,
            reference_date=date(2026, 7, 29),
            stage='COLLECT_NEWS',
            duration_seconds=1.23456,
            exception=exc,
        )

    record = caplog.records[-1]
    assert record.batch_event == 'failed'
    assert record.batch_job_id == 1001
    assert record.batch_page_id == 501
    assert record.batch_reference_date == date(2026, 7, 29)
    assert record.batch_stage == 'COLLECT_NEWS'
    assert record.batch_duration_seconds == 1.235
    assert record.batch_exception_class == 'TimeoutError'
    assert record.exc_info is None
    assert 'test_batch_logging.py' in record.batch_traceback
    assert 'test_log_batch_lifecycle_exposes_safe_structured_failure' in (
        record.batch_traceback
    )
    assert 'secret-token' not in caplog.text
