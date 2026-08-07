from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest  # pyright: ignore[reportMissingImports]
from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from tests.support import jsonable, load_module

batches_service_module = load_module('app.domains.batches.service')
projections_module = load_module('app.db.repositories.projections')
ai_retry_repo_module = load_module('app.db.repositories.ai_retry_repo')

BatchesService = batches_service_module.BatchesService
BatchJobListResult = projections_module.BatchJobListResult
BatchJobRecord = projections_module.BatchJobRecord
BatchJobSummary = projections_module.BatchJobSummary
BatchPageSource = projections_module.BatchPageSource
AiRetrySource = ai_retry_repo_module.AiRetrySource
AiRetryJob = ai_retry_repo_module.AiRetryJob
AiRetryEnqueueResult = ai_retry_repo_module.AiRetryEnqueueResult


class FakeBatchJobRepository:
    def __init__(
        self,
        *,
        active_exists: bool = False,
        page_exists: bool = False,
        page_status: str = 'READY',
        created_job: object | None = None,
        listed_jobs: object | None = None,
        detailed_job: object | None = None,
    ) -> None:
        self.active_exists = active_exists
        self.page_exists = page_exists
        self.page_status = page_status
        self.created_job = created_job
        self.listed_jobs = listed_jobs
        self.detailed_job = detailed_job
        self.created_params = None
        self.events: list[dict] = []
        self.commits = 0
        self.rollbacks = 0
        self.create_error: Exception | None = None
        self.idempotent_job = None
        self.session = object()
        self.list_jobs_kwargs: dict | None = None
        self.retried_job = None
        self.retry_failed_job_calls: list[int] = []
        self.retry_failed_job_error: Exception | None = None
        self.step_runs: list = []
        self.list_step_runs_calls: list[int] = []

    async def get_job_by_idempotency_key(self, idempotency_key):
        _ = idempotency_key
        return self.idempotent_job

    async def retry_failed_job(self, job_id):
        self.retry_failed_job_calls.append(job_id)
        if self.retry_failed_job_error is not None:
            raise self.retry_failed_job_error
        return self.retried_job

    async def has_active_job_for_business_date(self, business_date):
        _ = business_date
        return self.active_exists

    async def get_latest_page_source(self, business_date):
        _ = business_date
        if not self.page_exists:
            return None
        return BatchPageSource(page_id=501, batch_job_id=900, status=self.page_status)

    async def create_job(self, params):
        self.created_params = params
        if self.create_error is not None:
            raise self.create_error
        return self.created_job

    async def add_event(self, **kwargs):
        self.events.append(kwargs)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def list_jobs(self, **kwargs):
        self.list_jobs_kwargs = kwargs
        return self.listed_jobs

    async def get_job_by_id(self, job_id):
        _ = job_id
        return self.detailed_job

    async def list_step_runs(self, job_id):
        self.list_step_runs_calls.append(job_id)
        return self.step_runs


class FakeAiRetryEnqueuer:
    def __init__(self, *, source=None, result=None, error=None):
        self.source = source
        self.result = result
        self.error = error
        self.enqueue_kwargs = None
        self.commits = 0

    async def resolve_source(self, requested_job_id):
        assert requested_job_id == 1001
        return self.source

    async def enqueue(self, **kwargs):
        self.enqueue_kwargs = kwargs
        if self.error is not None:
            raise self.error
        return self.result

    async def commit(self):
        self.commits += 1


class FakeNewsCollectionRunRepository:
    def __init__(self, existing_run=None):
        self.existing_run = existing_run
        self.created_kwargs = None

    async def get_by_window(self, **_kwargs):
        return self.existing_run

    async def create_run(self, **kwargs):
        self.created_kwargs = kwargs
        self.existing_run = SimpleNamespace(
            run_id=41,
            batch_job_id=kwargs['batch_job_id'],
            provider_name=kwargs['provider_name'],
            window_start_at=kwargs['window_start_at'],
            window_end_at=kwargs['window_end_at'],
            query_start_at=kwargs['query_start_at'],
            query_end_at=kwargs['query_end_at'],
        )
        return self.existing_run


@pytest.mark.anyio
async def test_start_naver_news_collection_aligns_slot_and_enqueues_once():
    created_job = BatchJobRecord(
        job_id=3001,
        job_name='naver_news_collection',
        business_date=date(2026, 7, 31),
        status='PENDING',
        started_at=datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        queued_at=datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
    )
    batch_repo = FakeBatchJobRepository(created_job=created_job)
    run_repo = FakeNewsCollectionRunRepository()
    service = BatchesService(
        batch_repo,
        news_collection_repository=run_repo,
        now_factory=lambda: datetime(2026, 7, 31, 1, 3, 12, tzinfo=UTC),
    )

    result = await service.start_naver_news_collection(user_id='cron-admin')

    assert result['_created'] is True
    assert result['windowStartAt'].isoformat() == '2026-07-31T09:30:00+09:00'
    assert result['windowEndAt'].isoformat() == '2026-07-31T10:00:00+09:00'
    assert result['queryStartAt'].isoformat() == '2026-07-31T09:20:00+09:00'
    assert batch_repo.created_params.job_name == 'naver_news_collection'
    assert batch_repo.created_params.run_mode == 'NEWS_COLLECTION'
    assert batch_repo.commits == 1


@pytest.mark.anyio
async def test_start_naver_news_collection_reuses_same_slot_without_new_job():
    existing_run = SimpleNamespace(
        run_id=41,
        batch_job_id=3001,
        provider_name='NAVER_NEWS',
        window_start_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 31, 0, 20, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
    )
    existing_job = BatchJobRecord(
        job_id=3001,
        job_name='naver_news_collection',
        business_date=date(2026, 7, 31),
        status='RUNNING',
        started_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
    )
    batch_repo = FakeBatchJobRepository(detailed_job=existing_job)
    service = BatchesService(
        batch_repo,
        news_collection_repository=FakeNewsCollectionRunRepository(existing_run),
        now_factory=lambda: datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
    )

    result = await service.start_naver_news_collection(user_id='cron-admin')

    assert result['_created'] is False
    assert result['jobId'] == 3001
    assert batch_repo.created_params is None


@pytest.mark.anyio
async def test_start_naver_news_collection_accepts_bounded_historical_slot():
    created_job = BatchJobRecord(
        job_id=3002,
        job_name='naver_news_collection',
        business_date=date(2026, 7, 30),
        status='PENDING',
        started_at=datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
    )
    batch_repo = FakeBatchJobRepository(created_job=created_job)
    run_repo = FakeNewsCollectionRunRepository()
    service = BatchesService(
        batch_repo,
        news_collection_repository=run_repo,
        now_factory=lambda: datetime(2026, 7, 31, 1, 3, 12, tzinfo=UTC),
    )

    result = await service.start_naver_news_collection(
        user_id='cron-admin',
        slot_end_at=datetime(
            2026, 7, 30, 23, 30, tzinfo=batches_service_module.KST
        ),
    )

    assert result['windowStartAt'].isoformat() == '2026-07-30T23:00:00+09:00'
    assert result['windowEndAt'].isoformat() == '2026-07-30T23:30:00+09:00'
    assert run_repo.created_kwargs['window_end_at'] == datetime(
        2026, 7, 30, 23, 30, tzinfo=batches_service_module.KST
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ('slot_end_at', 'expected_code'),
    [
        (
            datetime(2026, 7, 31, 1, 30, tzinfo=UTC),
            'NEWS_SLOT_NOT_COMPLETED',
        ),
        (
            datetime(2026, 7, 23, 1, 0, tzinfo=UTC),
            'NEWS_SLOT_OUT_OF_RANGE',
        ),
        (
            datetime(2026, 7, 31, 0, 17, tzinfo=UTC),
            'NEWS_SLOT_INVALID',
        ),
    ],
)
async def test_start_naver_news_collection_rejects_invalid_historical_slot(
    slot_end_at,
    expected_code,
):
    service = BatchesService(
        FakeBatchJobRepository(),
        news_collection_repository=FakeNewsCollectionRunRepository(),
        now_factory=lambda: datetime(2026, 7, 31, 1, 3, 12, tzinfo=UTC),
    )

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_naver_news_collection(
            user_id='cron-admin',
            slot_end_at=slot_end_at,
        )

    assert exc_info.value.code == expected_code


@pytest.mark.anyio
async def test_start_market_daily_batch_enqueues_pending_job():
    repository = FakeBatchJobRepository(
        created_job=BatchJobRecord(
            job_id=1001,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='PENDING',
            started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
            ended_at=None,
            duration_seconds=None,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            force_run=False,
            rebuild_page_only=False,
        )
    )
    service = BatchesService(repository)

    result = await service.start_market_daily_batch(
        business_date=date(2026, 3, 17),
        user_id='test-user',
        force=False,
        rebuild_page_only=False,
    )

    assert isinstance(result, dict)
    payload = jsonable(result)
    assert payload['jobId'] == 1001
    assert payload['_created'] is True
    assert repository.created_params is not None
    assert repository.created_params.status == 'PENDING'
    assert repository.created_params.run_mode == 'FULL'
    assert repository.events[0]['step_code'] == 'CREATE_JOB'
    assert repository.commits == 1


@pytest.mark.anyio
async def test_start_market_daily_batch_rejects_duplicate_running_job():
    service = BatchesService(FakeBatchJobRepository(active_exists=True))

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert exc_info.value.code == 'BATCH_ALREADY_RUNNING'


@pytest.mark.anyio
async def test_start_market_daily_batch_converts_create_race_to_conflict():
    repository = FakeBatchJobRepository()
    repository.create_error = IntegrityError(
        'insert batch job', {}, Exception('duplicate')
    )
    service = BatchesService(repository)

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert exc_info.value.code == 'BATCH_ALREADY_RUNNING'
    assert repository.events == []
    assert repository.commits == 0


class _FakeDiag:
    def __init__(self, constraint_name: str) -> None:
        self.constraint_name = constraint_name


class _FakeDbError(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.diag = _FakeDiag(constraint_name)


@pytest.mark.anyio
async def test_start_market_daily_batch_reraises_unrelated_integrity_error():
    """An IntegrityError from an unrelated constraint must not be misreported
    as BATCH_ALREADY_RUNNING -- the real cause should surface instead."""
    repository = FakeBatchJobRepository()
    repository.create_error = IntegrityError(
        'insert batch job', {}, _FakeDbError('chk_batch_job_idempotency_key')
    )
    service = BatchesService(repository)

    with pytest.raises(IntegrityError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert 'chk_batch_job_idempotency_key' in str(exc_info.value.orig)


@pytest.mark.anyio
async def test_start_market_daily_batch_converts_known_conflict_constraint():
    repository = FakeBatchJobRepository()
    repository.create_error = IntegrityError(
        'insert batch job',
        {},
        _FakeDbError('uq_batch_job_one_active_market_daily_per_day'),
    )
    service = BatchesService(repository)

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert exc_info.value.code == 'BATCH_ALREADY_RUNNING'


@pytest.mark.anyio
async def test_start_market_daily_batch_rejects_existing_page_without_force():
    service = BatchesService(FakeBatchJobRepository(page_exists=True))

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert exc_info.value.code == 'PAGE_ALREADY_EXISTS'
    assert 'READY' in exc_info.value.message


@pytest.mark.anyio
async def test_start_market_daily_batch_reports_partial_page_status_on_conflict():
    """PAGE_ALREADY_EXISTS is raised even for a PARTIAL/FAILED page (existing
    behavior is kept), but the response must reveal the page's status so the
    caller isn't left guessing why a retry is being blocked."""
    service = BatchesService(
        FakeBatchJobRepository(page_exists=True, page_status='PARTIAL')
    )

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
        )

    assert exc_info.value.code == 'PAGE_ALREADY_EXISTS'
    assert 'PARTIAL' in exc_info.value.message


@pytest.mark.anyio
async def test_start_market_daily_batch_allows_existing_page_for_rebuild_without_force():
    repository = FakeBatchJobRepository(
        page_exists=True,
        created_job=BatchJobRecord(
            job_id=1002,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='PENDING',
            started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
            ended_at=None,
            duration_seconds=None,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            force_run=False,
            rebuild_page_only=True,
        ),
    )
    service = BatchesService(repository)

    result = await service.start_market_daily_batch(
        business_date=date(2026, 3, 17),
        user_id='test-user',
        force=False,
        rebuild_page_only=True,
    )

    assert result['jobId'] == 1002
    assert repository.created_params.rebuild_page_only is True
    assert repository.created_params.force_run is False
    assert repository.created_params.trigger_type == 'ADMIN_REBUILD'
    assert repository.created_params.run_mode == 'PAGE_REBUILD'
    assert repository.created_params.source_job_id == 900
    assert repository.created_params.source_page_id == 501


@pytest.mark.anyio
async def test_force_full_does_not_inherit_existing_page_lineage():
    repository = FakeBatchJobRepository(
        page_exists=True,
        created_job=BatchJobRecord(
            job_id=1003,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='PENDING',
            started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
            ended_at=None,
            duration_seconds=None,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            force_run=True,
            rebuild_page_only=False,
        ),
    )
    service = BatchesService(repository)

    await service.start_market_daily_batch(
        business_date=date(2026, 3, 17),
        user_id='test-user',
        force=True,
        rebuild_page_only=False,
    )

    assert repository.created_params.run_mode == 'FULL'
    assert repository.created_params.source_job_id is None
    assert repository.created_params.source_page_id is None


@pytest.mark.anyio
async def test_start_market_daily_batch_rejects_rebuild_without_existing_page():
    service = BatchesService(FakeBatchJobRepository(page_exists=False))

    with pytest.raises(batches_service_module.NotFoundError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=True,
        )

    assert exc_info.value.code == 'PAGE_NOT_FOUND'


@pytest.mark.anyio
async def test_start_market_daily_batch_replays_same_idempotency_key():
    existing_job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 17),
        status='PENDING',
        started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='FULL',
        idempotency_key='daily-2026-03-17',
    )
    repository = FakeBatchJobRepository()
    repository.idempotent_job = existing_job
    service = BatchesService(repository)

    result = await service.start_market_daily_batch(
        business_date=date(2026, 3, 17),
        user_id='test-user',
        force=False,
        rebuild_page_only=False,
        idempotency_key='daily-2026-03-17',
    )

    assert result['jobId'] == 1001
    assert result['_created'] is False
    assert repository.created_params is None
    assert repository.commits == 0


@pytest.mark.anyio
async def test_start_market_daily_batch_rejects_idempotency_key_reuse():
    existing_job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 16),
        status='SUCCESS',
        started_at=datetime(2026, 3, 17, 6, 10, tzinfo=UTC),
        ended_at=datetime(2026, 3, 17, 6, 20, tzinfo=UTC),
        duration_seconds=600,
        market_scope='GLOBAL',
        raw_news_count=1,
        processed_news_count=1,
        cluster_count=1,
        page_id=500,
        page_version_no=1,
        run_mode='FULL',
        idempotency_key='daily-key',
    )
    repository = FakeBatchJobRepository()
    repository.idempotent_job = existing_job
    service = BatchesService(repository)

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='test-user',
            force=False,
            rebuild_page_only=False,
            idempotency_key='daily-key',
        )

    assert exc_info.value.code == 'IDEMPOTENCY_KEY_REUSED'


@pytest.mark.anyio
async def test_start_market_daily_batch_retries_failed_job_with_same_idempotency_key():
    """H2 regression: cron uses a stable Idempotency-Key, so replaying it
    after the batch FAILED must requeue that job for another attempt --
    not silently hand back the stale FAILED job with no way to recover
    that day's batch.
    """
    failed_job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 17),
        status='FAILED',
        started_at=datetime(2026, 3, 17, 6, 10, tzinfo=UTC),
        ended_at=datetime(2026, 3, 17, 6, 20, tzinfo=UTC),
        duration_seconds=600,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='FULL',
        idempotency_key='daily-2026-03-17',
    )
    retried_job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 17),
        status='PENDING',
        started_at=datetime(2026, 3, 17, 6, 10, tzinfo=UTC),
        ended_at=None,
        duration_seconds=None,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='FULL',
        idempotency_key='daily-2026-03-17',
    )
    repository = FakeBatchJobRepository()
    repository.idempotent_job = failed_job
    repository.retried_job = retried_job
    service = BatchesService(repository)

    result = await service.start_market_daily_batch(
        business_date=date(2026, 3, 17),
        user_id='cron-admin',
        force=False,
        rebuild_page_only=False,
        idempotency_key='daily-2026-03-17',
    )

    assert repository.retry_failed_job_calls == [1001]
    assert result['status'] == 'PENDING'
    assert result['_created'] is True
    assert repository.commits == 1
    assert repository.created_params is None


@pytest.mark.anyio
async def test_start_market_daily_batch_failed_retry_conflicts_with_active_job():
    """F1 regression: retrying a FAILED job via idempotent replay must not
    surface the partial unique index violation
    (uq_batch_job_one_active_market_daily_per_day) as an unhandled
    IntegrityError/500. When another job for the same business_date is
    already PENDING/RUNNING, this must map to the existing
    BATCH_ALREADY_RUNNING conflict (409), and the session must be rolled
    back so it isn't left poisoned.
    """
    failed_job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 17),
        status='FAILED',
        started_at=datetime(2026, 3, 17, 6, 10, tzinfo=UTC),
        ended_at=datetime(2026, 3, 17, 6, 20, tzinfo=UTC),
        duration_seconds=600,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='FULL',
        idempotency_key='daily-2026-03-17',
    )
    repository = FakeBatchJobRepository()
    repository.idempotent_job = failed_job
    repository.retry_failed_job_error = IntegrityError(
        'UPDATE batch_job', {}, Exception('duplicate key value violates unique '
        'constraint "uq_batch_job_one_active_market_daily_per_day"')
    )
    service = BatchesService(repository)

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.start_market_daily_batch(
            business_date=date(2026, 3, 17),
            user_id='cron-admin',
            force=False,
            rebuild_page_only=False,
            idempotency_key='daily-2026-03-17',
        )

    assert exc_info.value.code == 'BATCH_ALREADY_RUNNING'
    assert repository.retry_failed_job_calls == [1001]
    assert repository.rollbacks == 1
    assert repository.commits == 0


@pytest.mark.anyio
async def test_list_jobs_returns_json_payload(sample_batch_job_list_payload):
    listed_jobs = BatchJobListResult(
        items=[
            BatchJobRecord(
                job_id=item['jobId'],
                job_name=item['jobName'],
                business_date=date.fromisoformat(item['businessDate']),
                status=item['status'],
                started_at=datetime.fromisoformat(item['startedAt']),
                ended_at=(
                    datetime.fromisoformat(item['endedAt'])
                    if item['endedAt'] is not None
                    else None
                ),
                duration_seconds=item['durationSeconds'],
                market_scope=item['marketScope'],
                raw_news_count=item['rawNewsCount'],
                processed_news_count=item['processedNewsCount'],
                cluster_count=item['clusterCount'],
                run_mode=item['runMode'],
                source_job_id=item['sourceJobId'],
                source_page_id=item['sourcePageId'],
                queued_at=datetime.fromisoformat(item['queuedAt']),
                attempt_count=item['attemptCount'],
                max_attempts=item['maxAttempts'],
                current_step=item['currentStep'],
                page_id=item['pageId'],
                page_version_no=item['pageVersionNo'],
                partial_message=item['partialMessage'],
            )
            for item in sample_batch_job_list_payload['items']
        ],
        page=sample_batch_job_list_payload['pagination']['page'],
        size=sample_batch_job_list_payload['pagination']['size'],
        total_count=sample_batch_job_list_payload['pagination']['totalCount'],
        summary=BatchJobSummary(
            success_count=sample_batch_job_list_payload['summary']['successCount'],
            partial_count=sample_batch_job_list_payload['summary']['partialCount'],
            failed_count=sample_batch_job_list_payload['summary']['failedCount'],
            avg_duration_seconds=sample_batch_job_list_payload['summary'][
                'avgDurationSeconds'
            ],
        ),
    )
    service = BatchesService(FakeBatchJobRepository(listed_jobs=listed_jobs))

    result = await service.list_jobs(
        from_date=date(2026, 3, 16),
        to_date=date(2026, 3, 17),
        status='SUCCESS',
        page=1,
        size=20,
    )

    assert isinstance(result, dict)
    assert result == sample_batch_job_list_payload


@pytest.mark.anyio
async def test_list_jobs_passes_job_type_to_repository(sample_batch_job_list_payload):
    listed_jobs = BatchJobListResult(
        items=[],
        page=1,
        size=20,
        total_count=0,
        summary=BatchJobSummary(
            success_count=0,
            partial_count=0,
            failed_count=0,
            avg_duration_seconds=0,
        ),
    )
    repository = FakeBatchJobRepository(listed_jobs=listed_jobs)
    service = BatchesService(repository)

    await service.list_jobs(
        from_date=None,
        to_date=None,
        status=None,
        job_type='NEWS_COLLECTION',
        page=1,
        size=20,
    )

    assert repository.list_jobs_kwargs['job_type'] == 'NEWS_COLLECTION'


@pytest.mark.anyio
async def test_get_job_detail_returns_json_payload(sample_batch_job_detail_payload):
    snapshot = sample_batch_job_detail_payload['snapshot']
    service = BatchesService(
        FakeBatchJobRepository(
            detailed_job=BatchJobRecord(
                job_id=sample_batch_job_detail_payload['jobId'],
                job_name=sample_batch_job_detail_payload['jobName'],
                business_date=date.fromisoformat(
                    sample_batch_job_detail_payload['businessDate']
                ),
                status=sample_batch_job_detail_payload['status'],
                started_at=datetime.fromisoformat(
                    sample_batch_job_detail_payload['startedAt']
                ),
                ended_at=datetime.fromisoformat(
                    sample_batch_job_detail_payload['endedAt']
                ),
                duration_seconds=sample_batch_job_detail_payload['durationSeconds'],
                market_scope='GLOBAL',
                raw_news_count=snapshot['rawNewsCount'],
                processed_news_count=snapshot['processedNewsCount'],
                cluster_count=snapshot['clusterCount'],
                run_mode=sample_batch_job_detail_payload['runMode'],
                source_job_id=sample_batch_job_detail_payload['sourceJobId'],
                source_page_id=sample_batch_job_detail_payload['sourcePageId'],
                queued_at=datetime.fromisoformat(
                    sample_batch_job_detail_payload['queuedAt']
                ),
                attempt_count=sample_batch_job_detail_payload['attemptCount'],
                max_attempts=sample_batch_job_detail_payload['maxAttempts'],
                current_step=sample_batch_job_detail_payload['currentStep'],
                page_id=snapshot['pageId'],
                page_version_no=snapshot['pageVersionNo'],
                force_run=snapshot['forceRun'],
                rebuild_page_only=snapshot['rebuildPageOnly'],
                partial_message=sample_batch_job_detail_payload['partialMessage'],
                error_code=sample_batch_job_detail_payload['errorCode'],
                error_message=sample_batch_job_detail_payload['errorMessage'],
                log_summary=sample_batch_job_detail_payload['logSummary'],
            )
        )
    )

    result = await service.get_job_detail(sample_batch_job_detail_payload['jobId'])

    assert isinstance(result, dict)
    assert result == sample_batch_job_detail_payload


@pytest.mark.anyio
async def test_get_job_detail_news_collection_fills_news_collection_not_snapshot():
    job = BatchJobRecord(
        job_id=3001,
        job_name='naver_news_collection',
        business_date=date(2026, 7, 31),
        status='SUCCESS',
        started_at=datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
        ended_at=datetime(2026, 7, 31, 1, 5, tzinfo=UTC),
        duration_seconds=120,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='NEWS_COLLECTION',
    )
    news_run = SimpleNamespace(
        run_id=41,
        batch_job_id=3001,
        provider_name='NAVER',
        window_start_at=datetime(2026, 7, 31, 0, 0, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        query_start_at=datetime(2026, 7, 30, 23, 50, tzinfo=UTC),
        query_end_at=datetime(2026, 7, 31, 0, 30, tzinfo=UTC),
        total_keyword_count=40,
        completed_keyword_count=40,
        fetched_count=900,
        matched_count=320,
        inserted_count=120,
        coverage_complete=True,
    )
    class FakeRunByJobIdRepository:
        def __init__(self, run: object) -> None:
            self.run = run
            self.calls: list[int] = []

        async def get_by_job_id(self, job_id: int):
            self.calls.append(job_id)
            return self.run

    run_repo = FakeRunByJobIdRepository(news_run)
    repository = FakeBatchJobRepository(detailed_job=job)
    service = BatchesService(repository, news_collection_repository=run_repo)

    result = await service.get_job_detail(3001)

    assert result['jobType'] == 'NEWS_COLLECTION'
    assert result['snapshot'] is None
    assert result['newsCollection']['runId'] == 41
    assert result['newsCollection']['providerName'] == 'NAVER'
    assert run_repo.calls == [3001]


@pytest.mark.anyio
async def test_get_job_detail_news_collection_missing_run_returns_null():
    job = BatchJobRecord(
        job_id=3002,
        job_name='naver_news_collection',
        business_date=date(2026, 7, 31),
        status='SUCCESS',
        started_at=datetime(2026, 7, 31, 1, 3, tzinfo=UTC),
        ended_at=datetime(2026, 7, 31, 1, 5, tzinfo=UTC),
        duration_seconds=120,
        market_scope='GLOBAL',
        raw_news_count=0,
        processed_news_count=0,
        cluster_count=0,
        page_id=None,
        page_version_no=None,
        run_mode='NEWS_COLLECTION',
    )

    class MissingRunRepository:
        async def get_by_job_id(self, job_id: int):
            _ = job_id
            return None

    repository = FakeBatchJobRepository(detailed_job=job)
    service = BatchesService(
        repository, news_collection_repository=MissingRunRepository()
    )

    result = await service.get_job_detail(3002)

    assert result['jobType'] == 'NEWS_COLLECTION'
    assert result['snapshot'] is None
    assert result['newsCollection'] is None


@pytest.mark.anyio
async def test_get_job_detail_snapshot_job_does_not_query_news_repo():
    job = BatchJobRecord(
        job_id=1001,
        job_name='market_daily_batch',
        business_date=date(2026, 3, 17),
        status='SUCCESS',
        started_at=datetime(2026, 3, 18, 6, 10, tzinfo=UTC),
        ended_at=datetime(2026, 3, 18, 6, 12, 15, tzinfo=UTC),
        duration_seconds=135,
        market_scope='GLOBAL',
        raw_news_count=174,
        processed_news_count=114,
        cluster_count=21,
        page_id=501,
        page_version_no=3,
        run_mode='PAGE_REBUILD',
    )

    class ExplodingRunRepository:
        async def get_by_job_id(self, job_id: int):
            raise AssertionError('news collection repo should not be queried')

    repository = FakeBatchJobRepository(detailed_job=job)
    service = BatchesService(
        repository, news_collection_repository=ExplodingRunRepository()
    )

    result = await service.get_job_detail(1001)

    assert result['jobType'] == 'MARKET_SNAPSHOT'
    assert result['snapshot'] is not None
    assert result['newsCollection'] is None


@pytest.mark.anyio
async def test_get_job_detail_includes_step_runs():
    repository = FakeBatchJobRepository(
        detailed_job=BatchJobRecord(
            job_id=1001,
            job_name='market_daily_batch',
            business_date=date(2026, 8, 7),
            status='SUCCESS',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 2, tzinfo=UTC),
            duration_seconds=120,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            run_mode='FULL',
        )
    )
    repository.step_runs = [
        SimpleNamespace(
            step_run_id=11,
            step_code='CREATE_JOB',
            seq=1,
            status='SUCCEEDED',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            duration_ms=1000,
        )
    ]
    service = BatchesService(repository)

    result = await service.get_job_detail(1001)

    assert result['steps'] == [
        {
            'stepCode': 'CREATE_JOB',
            'status': 'SUCCEEDED',
            'startedAt': '2026-08-07T00:00:00+00:00',
            'endedAt': '2026-08-07T00:00:01+00:00',
            'durationMs': 1000,
        }
    ]
    assert repository.list_step_runs_calls == [1001]


@pytest.mark.anyio
async def test_retry_ai_enqueues_pending_job_with_idempotency_key():
    source = AiRetrySource(
        requested_job_id=1001,
        source_job_id=1001,
        source_page_id=501,
        business_date=date(2026, 3, 17),
        source_status='PARTIAL',
    )
    retry_job = AiRetryJob(
        job_id=2001,
        job_name='market_daily_batch',
        business_date=source.business_date,
        status='PENDING',
        run_mode='AI_RETRY',
        source_job_id=source.source_job_id,
        source_page_id=source.source_page_id,
        idempotency_key='retry-key',
        started_at=datetime(2026, 3, 18, 6, 20, tzinfo=UTC),
    )
    enqueuer = FakeAiRetryEnqueuer(
        source=source,
        result=AiRetryEnqueueResult(retry_job, created=True),
    )
    repository = FakeBatchJobRepository()
    service = BatchesService(repository, ai_retry_enqueuer=enqueuer)

    result = await service.retry_ai_summaries(
        requested_job_id=1001,
        user_id='ADMIN-1',
        idempotency_key='retry-key',
    )

    assert result['jobId'] == 2001
    assert result['runMode'] == 'AI_RETRY'
    assert result['_created'] is True
    assert enqueuer.enqueue_kwargs['idempotency_key'] == 'retry-key'
    assert repository.events[0]['step_code'] == 'AI_RETRY_ENQUEUE'
    assert enqueuer.commits == 1


@pytest.mark.anyio
async def test_retry_ai_maps_idempotency_mismatch_to_conflict():
    source = AiRetrySource(
        requested_job_id=1001,
        source_job_id=1001,
        source_page_id=501,
        business_date=date(2026, 3, 17),
        source_status='PARTIAL',
    )
    enqueuer = FakeAiRetryEnqueuer(
        source=source,
        error=ai_retry_repo_module.AiRetryIdempotencyConflictError(),
    )
    service = BatchesService(FakeBatchJobRepository(), ai_retry_enqueuer=enqueuer)

    with pytest.raises(batches_service_module.ConflictError) as exc_info:
        await service.retry_ai_summaries(
            requested_job_id=1001,
            user_id='ADMIN-1',
            idempotency_key='reused-key',
        )

    assert exc_info.value.code == 'IDEMPOTENCY_KEY_REUSED'
