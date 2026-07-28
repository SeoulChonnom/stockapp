from __future__ import annotations

from datetime import UTC, date, datetime

import pytest  # pyright: ignore[reportMissingImports]

pytest.importorskip('sqlalchemy')

from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    jsonable,
    load_module,
    normalize_sql,
)

batch_repo_module = load_module('app.db.repositories.batch_job_repo')
projections_module = load_module('app.db.repositories.projections')

BatchJobRepository = batch_repo_module.BatchJobRepository
BatchJobCreateParams = projections_module.BatchJobCreateParams


class IntegrityErrorSession(RecordingAsyncSession):
    async def execute(self, statement, params=None):
        self.statements.append(statement)
        self.parameters.append(params)
        self.operations.append('execute')
        raise IntegrityError('insert batch job', params, Exception('duplicate'))


@pytest.mark.anyio
async def test_create_job_inserts_running_batch_row():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'job_id': 1001,
                        'job_name': 'market_daily_batch',
                        'business_date': date(2026, 3, 17),
                        'status': 'RUNNING',
                        'trigger_type': 'MANUAL',
                        'triggered_by_user_id': 'USER-0001',
                        'force_run': False,
                        'rebuild_page_only': False,
                        'started_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                        'ended_at': None,
                        'duration_seconds': None,
                        'market_scope': 'GLOBAL',
                        'raw_news_count': 0,
                        'processed_news_count': 0,
                        'cluster_count': 0,
                        'page_id': None,
                        'page_version_no': None,
                        'partial_message': None,
                        'error_code': None,
                        'error_message': None,
                        'log_summary': None,
                        'created_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                        'updated_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                    }
                ]
            )
        ]
    )
    repo = BatchJobRepository(session)

    result = await repo.create_job(
        BatchJobCreateParams(
            business_date=date(2026, 3, 17),
            status='RUNNING',
            trigger_type='MANUAL',
            triggered_by_user_id='USER-0001',
            force_run=False,
            rebuild_page_only=False,
        )
    )

    assert jsonable(result)['job_id'] == 1001
    assert jsonable(result)['triggered_by_user_id'] == 'USER-0001'
    assert session.operations == ['execute']
    sql = normalize_sql(session.statements[0])
    assert 'insert into stock.batch_job' in sql.lower()
    assert 'batch_job_status_enum' in sql
    statement_sql = ' '.join(str(session.statements[0]).split()).lower()
    assert 'cast(:triggered_by_user_id as text)' in statement_sql


@pytest.mark.anyio
async def test_create_job_rolls_back_integrity_error_before_reraising():
    session = IntegrityErrorSession()
    repo = BatchJobRepository(session)

    with pytest.raises(IntegrityError):
        await repo.create_job(
            BatchJobCreateParams(
                business_date=date(2026, 3, 17),
                status='RUNNING',
                trigger_type='MANUAL',
                triggered_by_user_id='USER-0001',
                force_run=False,
                rebuild_page_only=False,
            )
        )

    assert session.operations == ['execute', 'rollback']


@pytest.mark.anyio
async def test_terminalize_stale_active_jobs_marks_old_running_jobs_failed():
    session = RecordingAsyncSession()
    repo = BatchJobRepository(session)
    stale_before = datetime(2026, 3, 18, 0, 10, tzinfo=UTC)

    await repo.terminalize_stale_active_jobs(
        date(2026, 3, 17),
        stale_before=stale_before,
    )

    assert session.operations == ['execute']
    sql = normalize_sql(session.statements[0])
    assert 'update stock.batch_job' in sql.lower()
    assert "status in ('pending', 'running')" in sql.lower()
    assert 'started_at <' in sql.lower()
    assert session.parameters[0]['business_date'] == date(2026, 3, 17)
    assert session.parameters[0]['stale_before'] == stale_before
    assert session.parameters[0]['status'] == 'FAILED'
    assert session.parameters[0]['error_code'] == 'STALE_BATCH_JOB'


@pytest.mark.anyio
async def test_add_error_event_defers_commit_to_orchestrator():
    session = RecordingAsyncSession()
    repo = BatchJobRepository(session)

    await repo.add_event(
        job_id=1001,
        step_code='ORCHESTRATE',
        level='ERROR',
        message='Market daily batch orchestrator failed.',
        context_json={'error': 'provider timeout'},
    )

    assert session.operations == ['execute']
    sql = normalize_sql(session.statements[0])
    assert 'insert into stock.batch_job_event' in sql.lower()
    assert session.parameters[0]['level'] == 'ERROR'
    assert session.parameters[0]['step_code'] == 'ORCHESTRATE'
    assert session.parameters[0]['context_json'] == '{"error": "provider timeout"}'


@pytest.mark.anyio
async def test_mark_job_failed_defers_commit_to_orchestrator():
    session = RecordingAsyncSession()
    repo = BatchJobRepository(session)

    await repo.mark_job_failed(
        job_id=1001,
        error_code='INTERNAL_BATCH_ERROR',
        error_message='배치 오케스트레이터 실행 중 오류가 발생했습니다.',
    )

    assert session.operations == ['execute']
    sql = normalize_sql(session.statements[0])
    assert 'update stock.batch_job' in sql.lower()
    assert session.parameters[0]['status'] == 'FAILED'
    assert session.parameters[0]['error_code'] == 'INTERNAL_BATCH_ERROR'
    assert (
        session.parameters[0]['log_summary']
        == '배치 오케스트레이터 실행 중 오류가 발생했습니다.'
    )


@pytest.mark.anyio
async def test_list_jobs_casts_status_filter_to_enum():
    session = RecordingAsyncSession(
        results=[
            DummyResult([1]),
            DummyResult(
                [
                    {
                        'success_count': 1,
                        'partial_count': 0,
                        'failed_count': 0,
                        'avg_duration_seconds': 135,
                    }
                ]
            ),
            DummyResult(
                [
                    {
                        'job_id': 1001,
                        'job_name': 'market_daily_batch',
                        'business_date': date(2026, 3, 17),
                        'status': 'SUCCESS',
                        'started_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                        'ended_at': datetime(2026, 3, 18, 6, 12, tzinfo=UTC),
                        'duration_seconds': 135,
                        'market_scope': 'GLOBAL',
                        'raw_news_count': 174,
                        'processed_news_count': 114,
                        'cluster_count': 21,
                        'page_id': 501,
                        'page_version_no': 3,
                        'partial_message': None,
                    }
                ]
            ),
        ]
    )
    repo = BatchJobRepository(session)

    result = await repo.list_jobs(status='SUCCESS', page=1, size=20)

    assert result.total_count == 1
    sql = normalize_sql(session.statements[0])
    assert 'batch_job_status_enum' in sql


@pytest.mark.anyio
async def test_get_job_by_id_uses_batch_job_table(sample_batch_job_detail_payload):
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'job_id': sample_batch_job_detail_payload['jobId'],
                        'job_name': sample_batch_job_detail_payload['jobName'],
                        'business_date': date(2026, 3, 17),
                        'status': sample_batch_job_detail_payload['status'],
                        'trigger_type': 'MANUAL',
                        'triggered_by_user_id': 'USER-0001',
                        'force_run': False,
                        'rebuild_page_only': False,
                        'started_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                        'ended_at': datetime(2026, 3, 18, 6, 12, 15, tzinfo=UTC),
                        'duration_seconds': 135,
                        'market_scope': 'GLOBAL',
                        'raw_news_count': 174,
                        'processed_news_count': 114,
                        'cluster_count': 21,
                        'page_id': 501,
                        'page_version_no': 3,
                        'partial_message': None,
                        'error_code': None,
                        'error_message': None,
                        'log_summary': sample_batch_job_detail_payload['logSummary'],
                        'created_at': datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
                        'updated_at': datetime(2026, 3, 18, 6, 12, 15, tzinfo=UTC),
                    }
                ]
            )
        ]
    )
    repo = BatchJobRepository(session)

    result = await repo.get_job_by_id(sample_batch_job_detail_payload['jobId'])

    assert jsonable(result)['job_id'] == sample_batch_job_detail_payload['jobId']
    assert jsonable(result)['triggered_by_user_id'] == 'USER-0001'
    sql = normalize_sql(session.statements[0])
    assert 'stock.batch_job' in sql
