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
    assert 'where existing.target_key =' in sql
    assert session.parameters[0]['target_key'] == 'GLOBAL_HEADLINE'
    assert ':attempt_no' not in sql
