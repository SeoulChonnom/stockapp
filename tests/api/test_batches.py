from __future__ import annotations

import logging

import pytest  # pyright: ignore[reportMissingImports]

from tests.support import build_test_bearer_headers, load_module

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

batches_service_module = load_module('app.domains.batches.service')
batch_router_module = load_module('app.domains.batches.router')


class FakeBatchesService:
    def __init__(self, run_payload: dict, list_payload: dict, detail_payload: dict):
        self.run_payload = run_payload
        self.list_payload = list_payload
        self.detail_payload = detail_payload
        self.start_kwargs: dict | None = None
        self.retry_kwargs: dict | None = None
        self.retry_created = True
        self.lifecycle_events: list[str] = []

    async def start_market_daily_batch(self, **kwargs):
        self.start_kwargs = kwargs
        self.lifecycle_events.append('job_committed')
        return self.run_payload

    async def start_naver_news_collection(self, **kwargs):
        self.start_kwargs = kwargs
        self.lifecycle_events.append('job_committed')
        return {
            'jobId': 3001,
            'runId': 41,
            'jobName': 'naver_news_collection',
            'status': 'PENDING',
            'providerName': 'NAVER_NEWS',
            'windowStartAt': '2026-07-31T09:30:00+09:00',
            'windowEndAt': '2026-07-31T10:00:00+09:00',
            'queryStartAt': '2026-07-31T09:20:00+09:00',
            'queryEndAt': '2026-07-31T10:00:00+09:00',
            'queuedAt': '2026-07-31T10:00:03+09:00',
            '_created': self.retry_created,
        }

    async def list_jobs(self, **_kwargs):
        return self.list_payload

    async def get_job_detail(self, job_id: int):
        if job_id == self.detail_payload['jobId']:
            return self.detail_payload
        raise batches_service_module.NotFoundError(
            'BATCH_JOB_NOT_FOUND',
            '요청한 배치 작업을 찾을 수 없습니다.',
        )

    async def retry_ai_summaries(self, **kwargs):
        self.retry_kwargs = kwargs
        return {
            'jobId': 2001,
            'jobName': 'market_daily_batch',
            'businessDate': '2026-03-17',
            'status': 'PENDING',
            'runMode': 'AI_RETRY',
            'sourceJobId': kwargs['requested_job_id'],
            'sourcePageId': 501,
            'idempotencyKey': kwargs['idempotency_key'],
            'startedAt': '2026-03-18T06:20:00+00:00',
            '_created': self.retry_created,
        }


class FakeBatchScheduler:
    def __init__(self, lifecycle_events: list[str]):
        self.drain_calls = 0
        self.lifecycle_events = lifecycle_events
        self.failure: Exception | None = None

    def start_drain(self):
        self.drain_calls += 1
        self.lifecycle_events.append('drain_scheduled')
        if self.failure is not None:
            raise self.failure


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
    fake_scheduler = FakeBatchScheduler(fake_service.lifecycle_events)
    app.dependency_overrides[batch_router_module.get_batches_service] = lambda: (
        fake_service
    )
    app.dependency_overrides[batch_router_module.get_batch_scheduler] = lambda: (
        fake_scheduler
    )
    fake_service.batch_scheduler = fake_scheduler

    with TestClient(app) as test_client:
        yield test_client, fake_service

    app.dependency_overrides.clear()


def test_start_market_daily_batch_returns_job_handle(client, sample_batch_run_payload):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 202
    payload = response.json()['data']
    assert set(payload) == {
        'jobId',
        'jobName',
        'businessDate',
        'status',
        'startedAt',
        'queuedAt',
    }
    assert payload['jobId'] == sample_batch_run_payload['jobId']
    assert payload['status'] == 'PENDING'
    assert service.start_kwargs is not None
    assert service.start_kwargs['user_id'] == 'ADMIN-0001'
    assert service.batch_scheduler.drain_calls == 1
    assert service.lifecycle_events == ['job_committed', 'drain_scheduled']


def test_batch_apis_allow_client_role(client, sample_batch_job_detail_payload):
    test_client, service = client
    client_headers = build_test_bearer_headers('CLIENT')

    responses = [
        test_client.post(
            '/stock/api/batch/news-collection',
            headers=client_headers,
        ),
        test_client.post(
            '/stock/api/batch/market-daily',
            json={
                'businessDate': '2026-03-17',
                'force': False,
                'rebuildPageOnly': False,
            },
            headers=client_headers,
        ),
        test_client.get('/stock/api/batch/jobs', headers=client_headers),
        test_client.get(
            f'/stock/api/batch/jobs/{sample_batch_job_detail_payload["jobId"]}',
            headers=client_headers,
        ),
        test_client.post(
            '/stock/api/batch/jobs/1001/retry-ai',
            headers=client_headers,
        ),
    ]

    assert [response.status_code for response in responses] == [202, 202, 200, 200, 202]
    assert service.start_kwargs is not None
    assert service.start_kwargs['user_id'] == 'CLIENT-0001'
    assert service.retry_kwargs is not None
    assert service.retry_kwargs['user_id'] == 'CLIENT-0001'


def test_start_naver_news_collection_returns_aligned_job_handle(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/news-collection',
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 202
    payload = response.json()['data']
    assert payload['jobName'] == 'naver_news_collection'
    assert payload['windowStartAt'] == '2026-07-31T09:30:00+09:00'
    assert payload['windowEndAt'] == '2026-07-31T10:00:00+09:00'
    assert service.start_kwargs == {
        'user_id': 'ADMIN-0001',
        'slot_end_at': None,
    }
    assert service.batch_scheduler.drain_calls == 1


def test_start_naver_news_collection_replay_coalesces_drain(client):
    test_client, service = client
    service.retry_created = False

    response = test_client.post(
        '/stock/api/batch/news-collection',
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 202
    assert service.batch_scheduler.drain_calls == 1


def test_start_naver_news_collection_accepts_historical_aligned_slot(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/news-collection',
        json={'slotEndAt': '2026-07-30T23:30:00+09:00'},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 202
    assert service.start_kwargs is not None
    assert service.start_kwargs['slot_end_at'].isoformat() == (
        '2026-07-30T23:30:00+09:00'
    )
    assert service.batch_scheduler.drain_calls == 1


@pytest.mark.parametrize(
    'slot_end_at',
    [
        '2026-07-30T23:17:00+09:00',
        '2026-07-30T23:30:00',
    ],
)
def test_start_naver_news_collection_rejects_invalid_slot(client, slot_end_at):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/news-collection',
        json={'slotEndAt': slot_end_at},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 422
    assert service.start_kwargs is None
    assert service.batch_scheduler.drain_calls == 0


def test_start_naver_news_collection_rejects_user_as_forbidden(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/news-collection',
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 403
    assert response.json()['error']['code'] == 'AUTH_FORBIDDEN'
    assert service.start_kwargs is None
    assert service.batch_scheduler.drain_calls == 0


def test_start_naver_news_collection_rejects_missing_token(client):
    test_client, service = client

    response = test_client.post('/stock/api/batch/news-collection')

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_MISSING_BEARER_TOKEN'
    assert service.start_kwargs is None
    assert service.batch_scheduler.drain_calls == 0


def test_start_market_daily_batch_preserves_non_uuid_subject(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers={
            **build_test_bearer_headers('ADMIN', subject='USER-0001'),
            'Idempotency-Key': 'market-daily-2026-03-17',
        },
    )

    assert response.status_code == 202
    assert service.start_kwargs is not None
    assert service.start_kwargs['user_id'] == 'USER-0001'
    assert service.start_kwargs['idempotency_key'] == 'market-daily-2026-03-17'
    assert service.batch_scheduler.drain_calls == 1


def test_start_market_daily_batch_idempotent_replay_does_not_start_new_drain(client):
    test_client, service = client
    service.run_payload['_created'] = False

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers={
            **build_test_bearer_headers('ADMIN'),
            'Idempotency-Key': 'market-daily-2026-03-17',
        },
    )

    assert response.status_code == 202
    assert '_created' not in response.json()['data']
    assert service.batch_scheduler.drain_calls == 0


def test_start_market_daily_batch_keeps_202_when_drain_scheduling_fails(
    client,
    caplog,
):
    test_client, service = client
    service.batch_scheduler.failure = RuntimeError('sensitive scheduler detail')
    caplog.set_level(
        logging.ERROR,
        logger='app.domains.batches.router',
    )

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers=build_test_bearer_headers('ADMIN'),
    )

    assert response.status_code == 202
    assert 'exception_class=RuntimeError' in caplog.text
    assert 'sensitive scheduler detail' not in caplog.text


def test_start_market_daily_batch_rejects_blank_idempotency_key(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
        headers={
            **build_test_bearer_headers('ADMIN'),
            'Idempotency-Key': '   ',
        },
    )

    assert response.status_code == 422
    assert service.start_kwargs is None
    assert service.batch_scheduler.drain_calls == 0


def test_start_market_daily_batch_rejects_user_as_forbidden(client):
    test_client, _service = client

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


def test_start_market_daily_batch_rejects_missing_token_as_unauthorized(client):
    test_client, _service = client

    response = test_client.post(
        '/stock/api/batch/market-daily',
        json={'businessDate': '2026-03-17', 'force': False, 'rebuildPageOnly': False},
    )

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_MISSING_BEARER_TOKEN'
    assert response.json()['error']['message'] == 'Missing or invalid bearer token.'


def test_list_batch_jobs_allows_admin(client, sample_batch_job_list_payload):
    test_client, _service = client

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
        'runMode',
        'sourceJobId',
        'sourcePageId',
        'queuedAt',
        'attemptCount',
        'maxAttempts',
        'currentStep',
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
        'aiTargetCount',
        'aiAttemptedCount',
        'aiSuccessCount',
        'aiFallbackCount',
        'aiFailedCount',
        'aiRecoveredCount',
    } <= set(payload['items'][0])
    assert (
        payload['items'][0]['jobId']
        == sample_batch_job_list_payload['items'][0]['jobId']
    )
    assert payload['summary']['successCount'] == 17


def test_list_batch_jobs_rejects_user_as_forbidden(client):
    test_client, _service = client

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
    test_client, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs',
        params={'status': 'SUCCESS'},
        headers={'Authorization': 'Bearer definitely-not-a-valid-test-token'},
    )

    assert response.status_code == 401
    assert response.json()['error']['code'] == 'AUTH_INVALID_TOKEN'
    assert response.json()['error']['message'] == 'Access token is invalid.'


def test_get_batch_job_detail_allows_admin(client, sample_batch_job_detail_payload):
    test_client, _service = client

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
        'runMode',
        'sourceJobId',
        'sourcePageId',
        'queuedAt',
        'attemptCount',
        'maxAttempts',
        'currentStep',
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
        'aiTargetCount',
        'aiAttemptedCount',
        'aiSuccessCount',
        'aiFallbackCount',
        'aiFailedCount',
        'aiRecoveredCount',
    } <= set(payload)
    assert payload['jobId'] == sample_batch_job_detail_payload['jobId']
    assert payload['logSummary'] == sample_batch_job_detail_payload['logSummary']


def test_get_batch_job_detail_returns_404_when_missing(client):
    test_client, _service = client

    response = test_client.get(
        '/stock/api/batch/jobs/999', headers=build_test_bearer_headers('ADMIN')
    )

    assert response.status_code == 404


def test_retry_ai_enqueues_idempotent_job_and_drains_background_queue(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/jobs/1001/retry-ai',
        headers={
            **build_test_bearer_headers('ADMIN'),
            'Idempotency-Key': 'ai-retry-1001-request-1',
        },
    )

    assert response.status_code == 202
    payload = response.json()['data']
    assert payload['status'] == 'PENDING'
    assert payload['runMode'] == 'AI_RETRY'
    assert payload['sourceJobId'] == 1001
    assert payload['idempotencyKey'] == 'ai-retry-1001-request-1'
    assert service.retry_kwargs == {
        'requested_job_id': 1001,
        'user_id': 'ADMIN-0001',
        'idempotency_key': 'ai-retry-1001-request-1',
    }
    assert service.batch_scheduler.drain_calls == 1


def test_retry_ai_idempotent_replay_does_not_start_new_drain(client):
    test_client, service = client
    service.retry_created = False

    response = test_client.post(
        '/stock/api/batch/jobs/1001/retry-ai',
        headers={
            **build_test_bearer_headers('ADMIN'),
            'Idempotency-Key': 'ai-retry-1001-request-1',
        },
    )

    assert response.status_code == 202
    assert '_created' not in response.json()['data']
    assert service.batch_scheduler.drain_calls == 0


def test_retry_ai_requires_admin(client):
    test_client, service = client

    response = test_client.post(
        '/stock/api/batch/jobs/1001/retry-ai',
        headers=build_test_bearer_headers('USER'),
    )

    assert response.status_code == 403
    assert service.retry_kwargs is None
