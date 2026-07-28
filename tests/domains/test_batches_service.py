from __future__ import annotations

from datetime import UTC, date, datetime

import pytest  # pyright: ignore[reportMissingImports]
from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from tests.support import jsonable, load_module

batches_service_module = load_module('app.domains.batches.service')
projections_module = load_module('app.db.repositories.projections')

BatchesService = batches_service_module.BatchesService
BatchJobListResult = projections_module.BatchJobListResult
BatchJobRecord = projections_module.BatchJobRecord
BatchJobSummary = projections_module.BatchJobSummary
BatchPageSource = projections_module.BatchPageSource


class FakeBatchJobRepository:
    def __init__(
        self,
        *,
        active_exists: bool = False,
        page_exists: bool = False,
        created_job: object | None = None,
        listed_jobs: object | None = None,
        detailed_job: object | None = None,
    ) -> None:
        self.active_exists = active_exists
        self.page_exists = page_exists
        self.created_job = created_job
        self.listed_jobs = listed_jobs
        self.detailed_job = detailed_job
        self.created_params = None
        self.events: list[dict] = []
        self.commits = 0
        self.rollbacks = 0
        self.create_error: Exception | None = None
        self.idempotent_job = None

    async def get_job_by_idempotency_key(self, idempotency_key):
        _ = idempotency_key
        return self.idempotent_job

    async def has_active_job_for_business_date(self, business_date):
        _ = business_date
        return self.active_exists

    async def get_latest_page_source(self, business_date):
        _ = business_date
        if not self.page_exists:
            return None
        return BatchPageSource(page_id=501, batch_job_id=900)

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
        return self.listed_jobs

    async def get_job_by_id(self, job_id):
        _ = job_id
        return self.detailed_job


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
async def test_get_job_detail_returns_json_payload(sample_batch_job_detail_payload):
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
                raw_news_count=sample_batch_job_detail_payload['rawNewsCount'],
                processed_news_count=sample_batch_job_detail_payload[
                    'processedNewsCount'
                ],
                cluster_count=sample_batch_job_detail_payload['clusterCount'],
                page_id=sample_batch_job_detail_payload['pageId'],
                page_version_no=sample_batch_job_detail_payload['pageVersionNo'],
                force_run=sample_batch_job_detail_payload['forceRun'],
                rebuild_page_only=sample_batch_job_detail_payload['rebuildPageOnly'],
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
