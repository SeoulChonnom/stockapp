from __future__ import annotations

from datetime import UTC, date, datetime, timezone

import pytest

from tests.support import jsonable, load_module

batches_service_module = load_module('app.domains.batches.service')
projections_module = load_module('app.db.repositories.projections')

BatchesService = batches_service_module.BatchesService
BatchJobRecord = projections_module.BatchJobRecord


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

    async def has_active_job_for_business_date(self, business_date):
        return self.active_exists

    async def has_completed_page_for_business_date(self, business_date):
        return self.page_exists

    async def create_job(self, params):
        self.created_params = params
        return self.created_job

    async def add_event(self, **kwargs):
        self.events.append(kwargs)

    async def commit(self):
        self.commits += 1

    async def list_jobs(self, **kwargs):
        return self.listed_jobs

    async def get_job_by_id(self, job_id):
        _ = job_id
        return self.detailed_job


@pytest.mark.anyio
async def test_start_market_daily_batch_creates_running_job():
    repository = FakeBatchJobRepository(
        created_job=BatchJobRecord(
            job_id=1001,
            job_name='market_daily_batch',
            business_date=date(2026, 3, 17),
            status='RUNNING',
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

    payload = jsonable(result)
    assert payload['jobId'] == 1001
    assert repository.created_params.status == 'RUNNING'
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
