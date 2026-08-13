from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.db.repositories.ai_summary_write_repo import AiSummaryWriteRepository
from app.db.repositories.projections import AiSummaryCreateParams
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


def _success_row() -> dict:
    return {
        'summary_id': 7,
        'batch_job_id': 20,
        'summary_type': 'GLOBAL_HEADLINE',
        'business_date': date(2026, 7, 28),
        'market_type': None,
        'cluster_id': None,
        'title': 'successful title',
        'body': 'successful body',
        'paragraphs_json': [],
        'model_name': 'gemini',
        'prompt_version': 'v1',
        'status': 'SUCCESS',
        'fallback_used': False,
        'error_message': None,
        'metadata_json': {},
        'target_key': 'GLOBAL_HEADLINE',
        'source_summary_id': 1,
        'attempt_no': 2,
        'generated_at': datetime(2026, 7, 29, tzinfo=UTC),
    }


def _summary_params(
    *,
    batch_job_id: int = 30,
    target_key: str = 'GLOBAL_HEADLINE',
    attempt_no: int = 1,
) -> AiSummaryCreateParams:
    return AiSummaryCreateParams(
        batch_job_id=batch_job_id,
        summary_type='GLOBAL_HEADLINE',
        business_date=date(2026, 7, 28),
        market_type=None,
        cluster_id=None,
        title='full-run title',
        body='full-run body',
        paragraphs_json=[],
        model_name='gemini',
        prompt_version='v2',
        status='SUCCESS',
        fallback_used=False,
        error_message=None,
        metadata_json={},
        target_key=target_key,
        source_summary_id=None,
        attempt_no=attempt_no,
    )


@pytest.mark.anyio
async def test_retry_upsert_never_overwrites_existing_success():
    session = RecordingAsyncSession(
        results=[DummyResult([]), DummyResult([_success_row()])]
    )
    repository = AiSummaryWriteRepository(session)

    result = await repository.upsert_retry_summary(
        AiSummaryCreateParams(
            batch_job_id=20,
            summary_type='GLOBAL_HEADLINE',
            business_date=date(2026, 7, 28),
            market_type=None,
            cluster_id=None,
            title='fallback title',
            body=None,
            paragraphs_json=[],
            model_name='gemini',
            prompt_version='v1',
            status='FALLBACK',
            fallback_used=True,
            error_message='429',
            metadata_json={},
            target_key='GLOBAL_HEADLINE',
            source_summary_id=1,
            attempt_no=2,
        )
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'on conflict (batch_job_id, target_key) do update' in sql
    assert "where stock.ai_summary.status <> 'success'" in sql
    assert result.status == 'SUCCESS'
    assert result.title == 'successful title'


@pytest.mark.anyio
async def test_full_run_insert_allocates_next_attempt_per_logical_target():
    session = RecordingAsyncSession(results=[DummyResult([_success_row()])])
    repository = AiSummaryWriteRepository(session)

    await repository.insert_summary(
        AiSummaryCreateParams(
            batch_job_id=30,
            summary_type='GLOBAL_HEADLINE',
            business_date=date(2026, 7, 28),
            market_type=None,
            cluster_id=None,
            title='full-run title',
            body='full-run body',
            paragraphs_json=[],
            model_name='gemini',
            prompt_version='v2',
            status='SUCCESS',
            fallback_used=False,
            error_message=None,
            metadata_json={},
            target_key='GLOBAL_HEADLINE',
            source_summary_id=None,
        )
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'with target_lock as' in sql
    assert 'coalesce(max(existing.attempt_no), 0) + 1' in sql
    assert 'from target_lock left join stock.ai_summary as existing' in sql
    assert session.parameters[0]['target_key'] == 'GLOBAL_HEADLINE'
    assert ':attempt_no' not in sql


@pytest.mark.anyio
async def test_full_run_upsert_allocates_attempt_and_preserves_resume_attempt():
    session = RecordingAsyncSession(results=[DummyResult([_success_row()])])
    repository = AiSummaryWriteRepository(session)

    result = await repository.upsert_full_run_summary(
        _summary_params(batch_job_id=30, attempt_no=999)
    )

    sql = normalize_sql(session.statements[0]).lower()
    assert 'with target_lock as materialized' in sql
    assert 'next_attempt as materialized' in sql
    assert 'from target_lock left join stock.ai_summary as existing' in sql
    assert 'coalesce(max(existing.attempt_no), 0) + 1' in sql
    assert 'on conflict (batch_job_id, target_key) do update' in sql
    assert 'attempt_no = excluded.attempt_no' not in sql
    assert result.attempt_no == 2


@pytest.mark.anyio
async def test_full_run_first_insert_executes_materialized_lock_query_without_rows():
    session = RecordingAsyncSession(results=[DummyResult([_success_row()])])
    repository = AiSummaryWriteRepository(session)

    await repository.upsert_full_run_summary(_summary_params(batch_job_id=31))

    assert len(session.statements) == 1
    sql = normalize_sql(session.statements[0]).lower()
    assert 'target_lock as materialized' in sql
    assert 'select pg_advisory_xact_lock' in sql
    assert 'from target_lock left join stock.ai_summary as existing' in sql


@pytest.mark.anyio
async def test_full_run_empty_target_anchors_attempt_scan_on_lock_row():
    session = RecordingAsyncSession(results=[DummyResult([_success_row()])])
    repository = AiSummaryWriteRepository(session)

    await repository.upsert_full_run_summary(_summary_params(batch_job_id=32))

    sql = normalize_sql(session.statements[0]).lower()
    assert 'from target_lock left join stock.ai_summary as existing' in sql
    assert 'on existing.target_key =' in sql
    assert 'cross join target_lock' not in sql
    assert session.parameters[0]['target_key'] == 'GLOBAL_HEADLINE'
