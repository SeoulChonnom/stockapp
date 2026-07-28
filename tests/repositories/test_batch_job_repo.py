from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

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
async def test_create_job_inserts_pending_queue_row():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'job_id': 1001,
                        'job_name': 'market_daily_batch',
                        'business_date': date(2026, 3, 17),
                        'status': 'PENDING',
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
            status='PENDING',
            trigger_type='MANUAL',
            triggered_by_user_id='USER-0001',
            force_run=False,
            rebuild_page_only=False,
            run_mode='FULL',
            idempotency_key='daily-2026-03-17',
            max_attempts=3,
        )
    )

    assert jsonable(result)['job_id'] == 1001
    assert jsonable(result)['triggered_by_user_id'] == 'USER-0001'
    assert session.operations == ['execute']
    sql = normalize_sql(session.statements[0])
    assert 'insert into stock.batch_job' in sql.lower()
    assert 'batch_job_status_enum' in sql
    assert 'batch_run_mode_enum' in sql
    assert (
        session.statements[0].compile().params['idempotency_key']
        == 'daily-2026-03-17'
    )
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
                status='PENDING',
                trigger_type='MANUAL',
                triggered_by_user_id='USER-0001',
                force_run=False,
                rebuild_page_only=False,
            )
        )

    assert session.operations == ['execute', 'rollback']


@pytest.mark.anyio
async def test_recover_expired_claims_requeues_retryable_and_fails_exhausted():
    session = RecordingAsyncSession(results=[DummyResult([1]), DummyResult([2])])
    repo = BatchJobRepository(session)

    result = await repo.recover_expired_claims()

    assert result.failed_count == 1
    assert result.requeued_count == 2
    assert session.operations == ['execute', 'execute']
    failed_sql = normalize_sql(session.statements[0]).lower()
    requeued_sql = normalize_sql(session.statements[1]).lower()
    assert "status = 'failed'" in failed_sql
    assert 'attempt_count >= max_attempts' in failed_sql
    assert "status = 'pending'" in requeued_sql
    assert 'attempt_count < max_attempts' in requeued_sql


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


def _pending_queue_row(job_id: int) -> dict:
    queued_at = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)
    return {
        'job_id': job_id,
        'job_name': 'market_daily_batch',
        'business_date': date(2026, 7, 29),
        'status': 'RUNNING',
        'started_at': queued_at,
        'ended_at': None,
        'duration_seconds': None,
        'market_scope': 'GLOBAL',
        'raw_news_count': 0,
        'processed_news_count': 0,
        'cluster_count': 0,
        'page_id': None,
        'page_version_no': None,
        'run_mode': 'FULL',
        'queued_at': queued_at,
        'available_at': queued_at,
        'attempt_count': 1,
        'max_attempts': 3,
    }


@pytest.mark.anyio
async def test_claim_next_job_uses_skip_locked_and_workers_get_distinct_rows():
    first_session = RecordingAsyncSession(
        results=[DummyResult([_pending_queue_row(1001)])]
    )
    second_session = RecordingAsyncSession(
        results=[DummyResult([_pending_queue_row(1002)])]
    )
    first_repo = BatchJobRepository(first_session)
    second_repo = BatchJobRepository(second_session)

    first_job = await first_repo.claim_next_job(
        worker_id='worker-a',
        lease_token=uuid4(),
        lease_seconds=120,
    )
    second_job = await second_repo.claim_next_job(
        worker_id='worker-b',
        lease_token=uuid4(),
        lease_seconds=120,
    )

    assert first_job is not None and first_job.job_id == 1001
    assert second_job is not None and second_job.job_id == 1002
    for session in (first_session, second_session):
        sql = normalize_sql(session.statements[0]).lower()
        assert 'for update skip locked' in sql
        assert 'order by available_at, queued_at, id' in sql
        assert 'attempt_count = job.attempt_count + 1' in sql


@pytest.mark.anyio
async def test_heartbeat_and_checkpoint_updates_are_lease_fenced():
    lease_token = uuid4()
    session = RecordingAsyncSession(
        results=[DummyResult([1001]), DummyResult([1001]), DummyResult([1001])]
    )
    repo = BatchJobRepository(session)

    renewed = await repo.heartbeat_claim(
        job_id=1001,
        worker_id='worker-a',
        lease_token=lease_token,
        lease_seconds=120,
    )
    began = await repo.begin_step(
        job_id=1001,
        lease_token=lease_token,
        step_code='COLLECT_NEWS',
    )
    saved = await repo.save_checkpoint(
        job_id=1001,
        lease_token=lease_token,
        current_step='COLLECT_NEWS',
        checkpoint_json={
            'completedSteps': ['COLLECT_NEWS'],
            'context': {'rawNewsCount': 10},
        },
    )

    assert renewed is True
    assert began is True
    assert saved is True
    heartbeat_sql = ' '.join(str(session.statements[0]).split()).lower()
    checkpoint_sql = ' '.join(str(session.statements[2]).split()).lower()
    assert 'lease_owner = :worker_id' in heartbeat_sql
    assert 'lease_token = :lease_token' in heartbeat_sql
    assert 'lease_expires_at > now()' in heartbeat_sql
    assert 'lease_token = :lease_token' in checkpoint_sql
    assert 'lease_expires_at > now()' in checkpoint_sql
    assert '"completedSteps"' in session.parameters[2]['checkpoint_json']


@pytest.mark.anyio
async def test_fenced_completion_rejects_stale_lease_token():
    lease_token = uuid4()
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = BatchJobRepository(session, lease_token=lease_token)

    with pytest.raises(batch_repo_module.BatchLeaseLostError):
        await repo.mark_job_completed(job_id=1001, status='SUCCESS')

    sql = ' '.join(str(session.statements[0]).split()).lower()
    assert 'lease_token = :lease_token' in sql
    assert 'lease_expires_at > now()' in sql


@pytest.mark.anyio
async def test_release_failed_claim_is_token_fenced_and_preserves_checkpoint():
    lease_token = uuid4()
    session = RecordingAsyncSession(results=[DummyResult(['PENDING'])])
    repo = BatchJobRepository(session)

    status = await repo.release_failed_claim(
        job_id=1001,
        lease_token=lease_token,
        error_code='BATCH_ATTEMPT_FAILED',
        error_message='TimeoutError: provider timeout',
        retry_delay_seconds=30,
    )

    assert status == 'PENDING'
    sql = ' '.join(str(session.statements[0]).split()).lower()
    assert 'lease_token = :lease_token' in sql
    assert 'lease_expires_at > now()' in sql
    assert 'checkpoint_json' not in sql
    assert 'attempt_count >= max_attempts' in sql
