from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.db.repositories.ai_retry_repo import (
    AiRetryIdempotencyConflictError,
    AiRetrySource,
    PostgresAiRetryRepository,
)
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


def _job_row(
    *,
    source_job_id=10,
    source_page_id=501,
    idempotency_key='retry-key',
):
    return {
        'job_id': 20,
        'job_name': 'market_daily_batch',
        'business_date': date(2026, 7, 28),
        'status': 'PENDING',
        'run_mode': 'AI_RETRY',
        'source_job_id': source_job_id,
        'source_page_id': source_page_id,
        'idempotency_key': idempotency_key,
        'started_at': datetime(2026, 7, 29, tzinfo=UTC),
        'checkpoint_json': {},
        'page_id': None,
        'page_version_no': None,
        'ai_target_count': 0,
        'ai_attempted_count': 0,
        'ai_success_count': 0,
        'ai_fallback_count': 0,
        'ai_failed_count': 0,
        'ai_recovered_count': 0,
    }


def _source() -> AiRetrySource:
    return AiRetrySource(
        requested_job_id=10,
        source_job_id=10,
        source_page_id=501,
        business_date=date(2026, 7, 28),
        source_status='PARTIAL',
    )


@pytest.mark.anyio
async def test_enqueue_inserts_explicit_pending_ai_retry_contract():
    session = RecordingAsyncSession(results=[DummyResult([_job_row()])])
    repository = PostgresAiRetryRepository(session, max_attempts=5)

    result = await repository.enqueue(
        source=_source(),
        triggered_by_user_id='ADMIN-1',
        idempotency_key=None,
    )

    assert result.created is True
    sql = normalize_sql(session.statements[0]).lower()
    assert 'insert into stock.batch_job' in sql
    assert "'pending'" in sql
    assert "'ai_retry'" in sql
    assert 'source_job_id' in sql
    assert session.parameters[0]['source_page_id'] == 501
    assert session.parameters[0]['max_attempts'] == 5


@pytest.mark.anyio
async def test_enqueue_returns_existing_job_for_same_idempotency_identity():
    session = RecordingAsyncSession(results=[DummyResult([_job_row()])])
    repository = PostgresAiRetryRepository(session)

    result = await repository.enqueue(
        source=_source(),
        triggered_by_user_id='ADMIN-1',
        idempotency_key='retry-key',
    )

    assert result.created is False
    assert result.job.job_id == 20
    assert len(session.statements) == 1


@pytest.mark.anyio
async def test_enqueue_rejects_idempotency_key_for_different_source():
    session = RecordingAsyncSession(results=[DummyResult([_job_row(source_job_id=99)])])
    repository = PostgresAiRetryRepository(session)

    with pytest.raises(AiRetryIdempotencyConflictError):
        await repository.enqueue(
            source=_source(),
            triggered_by_user_id='ADMIN-1',
            idempotency_key='retry-key',
        )


@pytest.mark.anyio
async def test_enqueue_rejects_idempotency_key_for_different_source_page():
    session = RecordingAsyncSession(
        results=[DummyResult([_job_row(source_page_id=777)])]
    )
    repository = PostgresAiRetryRepository(session)

    with pytest.raises(AiRetryIdempotencyConflictError):
        await repository.enqueue(
            source=_source(),
            triggered_by_user_id='ADMIN-1',
            idempotency_key='retry-key',
        )


@pytest.mark.anyio
async def test_resolve_source_walks_retry_lineage_to_root_and_uses_requested_page():
    source_row = {
        'requested_job_id': 30,
        'source_job_id': 10,
        'source_page_id': 503,
        'business_date': date(2026, 7, 28),
        'source_status': 'SUCCESS',
    }
    session = RecordingAsyncSession(results=[DummyResult([source_row])])
    repository = PostgresAiRetryRepository(session)

    source = await repository.resolve_source(30)

    assert source == AiRetrySource(**source_row)
    sql = normalize_sql(session.statements[0]).lower()
    assert 'with recursive lineage as' in sql
    assert "lineage.run_mode in ('page_rebuild', 'ai_retry')" in sql
    assert 'cardinality(lineage.path) < 64' in sql
