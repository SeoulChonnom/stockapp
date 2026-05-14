from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from tests.support import build_test_bearer_headers, load_module

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

batches_service_module = load_module('app.domains.batches.service')


class FakeBatchesService:
    def __init__(self, run_payload: dict, list_payload: dict, detail_payload: dict):
        self.run_payload = run_payload
        self.list_payload = list_payload
        self.detail_payload = detail_payload
        self.start_kwargs: dict | None = None

    async def start_market_daily_batch(self, **kwargs):
        self.start_kwargs = kwargs
        return self.run_payload

    async def list_jobs(self, **_kwargs):
        return self.list_payload

    async def get_job_detail(self, job_id: int):
        if job_id == self.detail_payload['jobId']:
            return self.detail_payload
        raise batches_service_module.NotFoundError(
            'BATCH_JOB_NOT_FOUND',
            '요청한 배치 작업을 찾을 수 없습니다.',
        )


class FakeBatchScheduler:
    def __init__(self) -> None:
        self.job_ids: list[int] = []

    def schedule(self, background_tasks, job_id: int) -> None:
        _ = background_tasks
        self.job_ids.append(job_id)


@pytest.fixture
def client(
    app,
    sample_batch_run_payload,
    sample_batch_job_list_payload,
    sample_batch_job_detail_payload,
):
    fake_service = FakeBatchesService(
        sample_batch_run_payload,
        sample_batch_job_list_payload,
        sample_batch_job_detail_payload,
    )
    fake_scheduler = FakeBatchScheduler()
    app.dependency_overrides[batches_service_module.get_batches_service] = lambda: (
        fake_service
    )
    app.dependency_overrides[batches_service_module.get_batch_job_scheduler] = lambda: (
        fake_scheduler
    )

    with TestClient(app) as test_client:
        yield test_client, fake_scheduler, fake_service

    app.dependency_overrides.clear()


def test_start_market_daily_batch_returns_job_handle(client, sample_batch_run_payload):
    test_client, scheduler, service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert set(payload) == {'jobId', 'jobName', 'businessDate', 'status', 'startedAt'}
    assert payload['jobId'] == sample_batch_run_payload['jobId']
    assert payload['status'] == 'RUNNING'
    assert scheduler.job_ids == [sample_batch_run_payload['jobId']]
    assert service.start_kwargs is not None
    assert service.start_kwargs['user_id'] == 'ADMIN-0001'


def test_start_market_daily_batch_preserves_non_uuid_subject(client):
    test_client, scheduler, service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers=build_test_bearer_headers('ADMIN', subject='USER-0001'),
    )

    assert response.status_code == 200
    assert scheduler.job_ids == [service.run_payload['jobId']]
    assert service.start_kwargs is not None
    assert service.start_kwargs['user_id'] == 'USER-0001'


def test_start_market_daily_batch_rejects_user_as_forbidden(client):
    test_client, scheduler, _service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 403
    assert response.json()['error']['code'] == 'AUTH_FORBIDDEN'
    assert (
        response.json()['error']['message']
        == 'You do not have permission to access this resource.'
    )
    assert scheduler.job_ids == []


def test_start_market_daily_batch_rejects_missing_token_as_unauthorized(client):
    test_client, scheduler, _service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
    )

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_MISSING_BEARER_TOKEN'
    assert response.json()['error']['message'] == 'Missing or invalid bearer token.'
    assert scheduler.job_ids == []


def test_list_batch_jobs_allows_admin(client, sample_batch_job_list_payload):
    test_client, _scheduler, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs',
        params={'status': 'SUCCESS'},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert set(payload) == {'items', 'pagination', 'summary'}
    assert {
        'jobId',
        'jobName',
        'businessDate',
        'status',
        'startedAt',
        'endedAt',
        'durationSeconds',
        'marketScope',
        'rawNewsCount',
        'processedNewsCount',
        'clusterCount',
        'pageId',
        'pageVersionNo',
        'partialMessage',
    } <= set(payload['items'][0])
    assert (
        payload['items'][0]['jobId']
        == sample_batch_job_list_payload['items'][0]['jobId']
    )
    assert payload['summary']['successCount'] == 17


def test_list_batch_jobs_rejects_user_as_forbidden(client):
    test_client, _scheduler, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs',
        params={'status': 'SUCCESS'},
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 403
    assert response.json()['error']['code'] == 'AUTH_FORBIDDEN'
    assert (
        response.json()['error']['message']
        == 'You do not have permission to access this resource.'
    )


def test_list_batch_jobs_rejects_invalid_token_as_unauthorized(client):
    test_client, _scheduler, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs',
        params={'status': 'SUCCESS'},
        headers={'Authorization': 'Bearer definitely-not-a-valid-test-token'},
    )

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_INVALID_TOKEN'
    assert response.json()['error']['message'] == 'Access token is invalid.'


def test_get_batch_job_detail_allows_admin(client, sample_batch_job_detail_payload):
    test_client, _scheduler, _service = client

    response = test_client.get(
        f'/stock/api/batch/jobs/{sample_batch_job_detail_payload["jobId"]}',
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert {
        'jobId',
        'jobName',
        'businessDate',
        'status',
        'forceRun',
        'rebuildPageOnly',
        'startedAt',
        'endedAt',
        'durationSeconds',
        'rawNewsCount',
        'processedNewsCount',
        'clusterCount',
        'pageId',
        'pageVersionNo',
        'partialMessage',
        'errorCode',
        'errorMessage',
        'logSummary',
    } <= set(payload)
    assert payload['jobId'] == sample_batch_job_detail_payload['jobId']
    assert payload['logSummary'] == sample_batch_job_detail_payload['logSummary']


def test_get_batch_job_detail_returns_404_when_missing(client):
    test_client, _scheduler, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs/999', headers=build_test_bearer_headers('ADMIN')
    )

    assert response.status_code == 404
