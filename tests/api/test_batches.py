from __future__ import annotations

import pytest

from tests.support import load_module

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

batches_service_module = load_module("app.domains.batches.service")
auth_module = load_module("app.api.deps.auth")


class FakeBatchesService:
    def __init__(self, run_payload: dict, list_payload: dict, detail_payload: dict):
        self.run_payload = run_payload
        self.list_payload = list_payload
        self.detail_payload = detail_payload

    async def start_market_daily_batch(self, **_kwargs):
        return self.run_payload

    async def list_jobs(self, **_kwargs):
        return self.list_payload

    async def get_job_detail(self, job_id: int):
        if job_id == self.detail_payload["jobId"]:
            return self.detail_payload
        raise batches_service_module.NotFoundError(
            "BATCH_JOB_NOT_FOUND",
            "요청한 배치 작업을 찾을 수 없습니다.",
        )


class FakeBatchScheduler:
    def __init__(self) -> None:
        self.job_ids: list[int] = []

    def schedule(self, background_tasks, job_id: int) -> None:
        _ = background_tasks
        self.job_ids.append(job_id)


@pytest.fixture
def client(app, sample_batch_run_payload, sample_batch_job_list_payload, sample_batch_job_detail_payload):
    fake_service = FakeBatchesService(
        sample_batch_run_payload,
        sample_batch_job_list_payload,
        sample_batch_job_detail_payload,
    )
    fake_scheduler = FakeBatchScheduler()
    app.dependency_overrides[auth_module.get_current_user] = lambda: {"user_id": "test-user"}
    app.dependency_overrides[batches_service_module.get_batches_service] = lambda: fake_service
    app.dependency_overrides[batches_service_module.get_batch_job_scheduler] = lambda: fake_scheduler

    with TestClient(app) as test_client:
        yield test_client, fake_scheduler

    app.dependency_overrides.clear()


def test_start_market_daily_batch_returns_job_handle(client, sample_batch_run_payload):
    test_client, scheduler = client

    response = test_client.post(
        "/stock/api/batch/market-daily",
        json={"businessDate": "2026-03-17", "force": False, "rebuildPageOnly": False},
    )

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["jobId"] == sample_batch_run_payload["jobId"]
    assert payload["status"] == "RUNNING"
    assert scheduler.job_ids == [sample_batch_run_payload["jobId"]]


def test_list_batch_jobs_returns_table_payload(client, sample_batch_job_list_payload):
    test_client, _scheduler = client

    response = test_client.get("/stock/api/batch/jobs", params={"status": "SUCCESS"})

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["items"][0]["jobId"] == sample_batch_job_list_payload["items"][0]["jobId"]
    assert payload["summary"]["successCount"] == 17


def test_get_batch_job_detail_returns_contract(client, sample_batch_job_detail_payload):
    test_client, _scheduler = client

    response = test_client.get(f"/stock/api/batch/jobs/{sample_batch_job_detail_payload['jobId']}")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["jobId"] == sample_batch_job_detail_payload["jobId"]
    assert payload["logSummary"] == sample_batch_job_detail_payload["logSummary"]


def test_get_batch_job_detail_returns_404_when_missing(client):
    test_client, _scheduler = client

    response = test_client.get("/stock/api/batch/jobs/999")

    assert response.status_code == 404
