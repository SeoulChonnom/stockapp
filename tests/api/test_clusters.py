from __future__ import annotations

import pytest  # pyright: ignore[reportMissingImports]

from tests.support import load_module

pytest.importorskip('fastapi')
from fastapi.testclient import TestClient  # pyright: ignore[reportMissingImports]

clusters_service_module = load_module('app.domains.clusters.service')
auth_module = load_module('app.api.deps.auth')


class FakeClustersService:
    def __init__(self, payload: dict):
        self.payload = payload

    async def get_cluster_detail(self, cluster_id):
        if str(cluster_id) == self.payload['clusterId']:
            return self.payload
        return None


@pytest.fixture
def client(app, sample_cluster_detail_payload):
    fake_clusters_service = FakeClustersService(sample_cluster_detail_payload)
    app.dependency_overrides[auth_module.get_current_user] = lambda: (
        auth_module.CurrentUser(
            user_id='test-user',
            roles=('USER',),
        )
    )
    app.dependency_overrides[clusters_service_module.get_clusters_service] = lambda: (
        fake_clusters_service
    )

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_get_cluster_detail_returns_contract(client, sample_cluster_detail_payload):
    response = client.get(
        f'/stock/api/news/clusters/{sample_cluster_detail_payload["clusterId"]}'
    )

    assert response.status_code == 200
    payload = response.json()['data']
    assert payload['clusterId'] == sample_cluster_detail_payload['clusterId']
    assert payload['marketType'] == 'US'
    assert payload['representativeArticle']['publisherName'] == '매일경제'
    assert payload['articles'][1]['title'] == '엔비디아 강세에 반도체 섹터 동반 상승'


def test_get_cluster_detail_rejects_malformed_uuid(client):
    response = client.get('/stock/api/news/clusters/not-a-uuid')

    assert response.status_code == 422


def test_get_cluster_detail_returns_404_when_missing(client):
    response = client.get(
        '/stock/api/news/clusters/7b9845f6-5c3d-4f2c-a81d-8dcb0b5dd6d2'
    )

    assert response.status_code == 404
