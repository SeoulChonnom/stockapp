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
        session.statements[0].compile().params['idempotency_key'] == 'daily-2026-03-17'
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
    session = RecordingAsyncSession(
        results=[DummyResult([1]), DummyResult([2]), DummyResult([3])]
    )
    repo = BatchJobRepository(session)

    result = await repo.recover_expired_claims()

    assert result.failed_count == 1
    assert result.requeued_count == 2
    assert session.operations == ['execute', 'execute', 'execute']
    failed_sql = normalize_sql(session.statements[0]).lower()
    requeued_sql = normalize_sql(session.statements[1]).lower()
    assert "status = 'failed'" in failed_sql
    assert 'attempt_count >= max_attempts' in failed_sql
    assert "status = 'pending'" in requeued_sql
    assert 'attempt_count < max_attempts' in requeued_sql
    assert 'lease_expires_at is null' in failed_sql
    assert 'lease_expires_at is null' in requeued_sql


@pytest.mark.anyio
async def test_seconds_until_next_actionable_job_includes_retry_and_lease_times():
    session = RecordingAsyncSession(results=[DummyResult([12.5])])
    repo = BatchJobRepository(session)

    delay_seconds = await repo.seconds_until_next_actionable_job()

    assert delay_seconds == 12.5
    sql = normalize_sql(session.statements[0]).lower()
    assert 'min(action_at)' in sql
    assert 'available_at as action_at' in sql
    assert 'coalesce(lease_expires_at, now()) as action_at' in sql
    assert "status = 'pending'" in sql
    assert "status = 'running'" in sql
    assert 'attempt_count < max_attempts' in sql


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
async def test_list_jobs_filters_news_collection_job_type():
    session = RecordingAsyncSession(
        results=[
            DummyResult([1]),
            DummyResult(
                [
                    {
                        'success_count': 1,
                        'partial_count': 0,
                        'failed_count': 0,
                        'avg_duration_seconds': 100,
                    }
                ]
            ),
            DummyResult([]),
        ]
    )
    repo = BatchJobRepository(session)

    await repo.list_jobs(job_type='NEWS_COLLECTION', page=1, size=20)

    count_sql = str(session.statements[0]).lower()
    assert 'batch_run_mode_enum' in count_sql
    assert 'run_mode = cast(:job_type_run_mode as' in count_sql
    assert session.parameters[0]['job_type_run_mode'] == 'NEWS_COLLECTION'


@pytest.mark.anyio
async def test_list_jobs_filters_market_snapshot_job_type():
    session = RecordingAsyncSession(
        results=[
            DummyResult([2]),
            DummyResult(
                [
                    {
                        'success_count': 2,
                        'partial_count': 0,
                        'failed_count': 0,
                        'avg_duration_seconds': 100,
                    }
                ]
            ),
            DummyResult([]),
        ]
    )
    repo = BatchJobRepository(session)

    await repo.list_jobs(job_type='MARKET_SNAPSHOT', page=1, size=20)

    count_sql = normalize_sql(session.statements[0]).lower()
    assert 'run_mode in (' in count_sql
    assert count_sql.count('batch_run_mode_enum') == 3
    assert session.parameters[0] == {
        'job_type_run_mode_0': 'FULL',
        'job_type_run_mode_1': 'PAGE_REBUILD',
        'job_type_run_mode_2': 'AI_RETRY',
    }


@pytest.mark.anyio
async def test_list_jobs_without_job_type_matches_previous_sql():
    session = RecordingAsyncSession(
        results=[
            DummyResult([3]),
            DummyResult(
                [
                    {
                        'success_count': 3,
                        'partial_count': 0,
                        'failed_count': 0,
                        'avg_duration_seconds': 100,
                    }
                ]
            ),
            DummyResult([]),
        ]
    )
    repo = BatchJobRepository(session)

    await repo.list_jobs(page=1, size=20)

    count_sql = normalize_sql(session.statements[0]).lower()
    assert 'run_mode' not in count_sql
    assert session.parameters[0] == {}


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
        results=[
            DummyResult([1001]),
            DummyResult([1001]),
            DummyResult([777]),
            DummyResult([1001]),
        ]
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
    assert began == 777
    assert saved is True
    heartbeat_sql = ' '.join(str(session.statements[0]).split()).lower()
    checkpoint_sql = ' '.join(str(session.statements[3]).split()).lower()
    assert 'lease_owner = :worker_id' in heartbeat_sql
    assert 'lease_token = :lease_token' in heartbeat_sql
    assert 'lease_expires_at > now()' in heartbeat_sql
    assert 'lease_token = :lease_token' in checkpoint_sql
    assert 'lease_expires_at > now()' in checkpoint_sql
    assert '"completedSteps"' in session.parameters[3]['checkpoint_json']


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
async def test_mark_job_completed_persists_full_ai_counts():
    session = RecordingAsyncSession(results=[DummyResult([1001])])
    repo = BatchJobRepository(session)

    await repo.mark_job_completed(
        job_id=1001,
        status='PARTIAL',
        ai_target_count=6,
        ai_attempted_count=6,
        ai_success_count=4,
        ai_fallback_count=1,
        ai_failed_count=1,
    )

    sql = ' '.join(str(session.statements[0]).split()).lower()
    assert 'ai_target_count = :ai_target_count' in sql
    assert 'ai_attempted_count = :ai_attempted_count' in sql
    assert 'ai_success_count = :ai_success_count' in sql
    assert 'ai_fallback_count = :ai_fallback_count' in sql
    assert 'ai_failed_count = :ai_failed_count' in sql
    assert session.parameters[0]['ai_target_count'] == 6
    assert session.parameters[0]['ai_attempted_count'] == 6
    assert session.parameters[0]['ai_success_count'] == 4
    assert session.parameters[0]['ai_fallback_count'] == 1
    assert session.parameters[0]['ai_failed_count'] == 1


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


@pytest.mark.anyio
async def test_begin_step_records_step_run_and_returns_its_id():
    lease_token = uuid4()
    session = RecordingAsyncSession(results=[DummyResult([1001]), DummyResult([777])])
    repo = BatchJobRepository(session)

    step_run_id = await repo.begin_step(
        job_id=1001,
        lease_token=lease_token,
        step_code='COLLECT_NEWS',
    )

    assert step_run_id == 777
    lease_sql = normalize_sql(session.statements[0]).lower()
    assert 'update stock.batch_job' in lease_sql
    assert 'lease_expires_at > now()' in lease_sql
    insert_sql = normalize_sql(session.statements[1]).lower()
    assert 'insert into stock.batch_job_step_run' in insert_sql
    assert 'coalesce(max(seq), 0) + 1' in insert_sql
    assert session.parameters[1] == {
        'job_id': 1001,
        'step_code': 'COLLECT_NEWS',
    }


@pytest.mark.anyio
async def test_begin_step_returns_none_and_skips_insert_when_lease_lost():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = BatchJobRepository(session)

    step_run_id = await repo.begin_step(
        job_id=1001,
        lease_token=uuid4(),
        step_code='COLLECT_NEWS',
    )

    assert step_run_id is None
    assert len(session.statements) == 1


@pytest.mark.anyio
async def test_finish_step_run_persists_diagnostics_for_failed_running_row():
    session = RecordingAsyncSession(results=[DummyResult([777])])
    repo = BatchJobRepository(session)

    finished = await repo.finish_step_run(
        step_run_id=777,
        status='FAILED',
        error_message='External provider request failed.',
        error_log='Traceback ... [REDACTED]',
    )

    assert finished is True
    sql = ' '.join(str(session.statements[0]).split()).lower()
    assert 'update stock.batch_job_step_run' in sql
    assert 'ended_at = now()' in sql
    assert "where id = :step_run_id and status = 'running'" in sql
    assert session.parameters[0]['step_run_id'] == 777
    assert session.parameters[0]['status'] == 'FAILED'
    assert session.parameters[0]['error_message'] == 'External provider request failed.'
    assert session.parameters[0]['error_log'] == 'Traceback ... [REDACTED]'


@pytest.mark.anyio
async def test_finish_step_run_clears_diagnostics_for_succeeded_row():
    session = RecordingAsyncSession(results=[DummyResult([777])])
    repo = BatchJobRepository(session)

    finished = await repo.finish_step_run(
        step_run_id=777,
        status='SUCCEEDED',
        error_message='Should not be persisted.',
        error_log='Should not be persisted.',
    )

    assert finished is True
    assert session.parameters[0]['error_message'] is None
    assert session.parameters[0]['error_log'] is None


@pytest.mark.anyio
async def test_finish_step_run_returns_false_when_already_closed():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = BatchJobRepository(session)

    finished = await repo.finish_step_run(step_run_id=777, status='FAILED')

    assert finished is False


@pytest.mark.anyio
async def test_list_step_runs_returns_records_in_seq_order():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'step_run_id': 11,
                        'step_code': 'CREATE_JOB',
                        'seq': 1,
                        'status': 'SUCCEEDED',
                        'started_at': datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
                        'ended_at': datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
                        'duration_ms': 1000,
                        'error_message': 'External provider request failed.',
                        'error_log': 'Traceback ... [REDACTED]',
                    },
                    {
                        'step_run_id': 12,
                        'step_code': 'DEDUPE_ARTICLES',
                        'seq': 2,
                        'status': 'RUNNING',
                        'started_at': datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
                        'ended_at': None,
                        'duration_ms': None,
                        'error_message': None,
                        'error_log': None,
                    },
                ]
            )
        ]
    )
    repo = BatchJobRepository(session)

    records = await repo.list_step_runs(1001)

    assert [record.step_code for record in records] == [
        'CREATE_JOB',
        'DEDUPE_ARTICLES',
    ]
    assert records[0].duration_ms == 1000
    assert records[0].error_message == 'External provider request failed.'
    assert records[0].error_log == 'Traceback ... [REDACTED]'
    assert records[1].ended_at is None
    assert records[1].error_message is None
    assert records[1].error_log is None
    sql = normalize_sql(session.statements[0]).lower()
    assert 'from stock.batch_job_step_run' in sql
    assert 'order by seq' in sql
    assert session.parameters[0] == {'job_id': 1001}


@pytest.mark.anyio
async def test_recover_expired_claims_closes_orphan_step_runs():
    session = RecordingAsyncSession(
        results=[DummyResult([1]), DummyResult([2]), DummyResult([3])]
    )
    repo = BatchJobRepository(session)

    await repo.recover_expired_claims()

    orphan_sql = normalize_sql(session.statements[2]).lower()
    assert 'update stock.batch_job_step_run' in orphan_sql
    assert "sr.status = 'running'" in orphan_sql
    assert "j.status <> 'running'" in orphan_sql
    assert 'error_message = ' in orphan_sql
    assert 'batch worker stopped before the step completed.' in orphan_sql
    assert 'error_log = ' in orphan_sql
    assert 'batch step was closed during expired worker lease recovery.' in orphan_sql
