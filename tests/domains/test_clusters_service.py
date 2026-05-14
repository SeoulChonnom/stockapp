from __future__ import annotations

import pytest

from tests.support import jsonable, load_module

clusters_service_module = load_module('app.domains.clusters.service')

ClustersService = clusters_service_module.ClustersService


class FakeClusterRepository:
    def __init__(self, cluster_row, cluster_articles, processed_articles):
        self.cluster_row = cluster_row
        self.cluster_articles = cluster_articles
        self.processed_articles = processed_articles
        self.calls: list[tuple] = []

    async def get_cluster_by_uid(self, cluster_uid):
        self.calls.append(('get_cluster_by_uid', str(cluster_uid)))
        return (
            self.cluster_row
            if str(cluster_uid) == self.cluster_row['cluster_uid']
            else None
        )

    async def get_cluster_articles(self, cluster_id):
        self.calls.append(('get_cluster_articles', cluster_id))
        return self.cluster_articles

    async def get_processed_articles(self, article_ids):
        self.calls.append(('get_processed_articles', tuple(article_ids)))
        return [self.processed_articles[article_id] for article_id in article_ids]


@pytest.fixture
def cluster_repository(
    sample_cluster_row, sample_cluster_article_rows, sample_processed_article_rows
):
    processed_articles = {row['id']: row for row in sample_processed_article_rows}
    return FakeClusterRepository(
        sample_cluster_row, sample_cluster_article_rows, processed_articles
    )


@pytest.mark.anyio
async def test_cluster_service_returns_cluster_detail(
    cluster_repository, sample_cluster_detail_payload
):
    service = ClustersService(cluster_repository)

    result = await service.get_cluster_detail(
        sample_cluster_detail_payload['clusterId']
    )
    payload = jsonable(result)

    assert payload['clusterId'] == sample_cluster_detail_payload['clusterId']
    assert payload['representativeArticle']['title'] == '엔비디아 급등에 반도체 강세'
    assert payload['articles'][0]['title'] == '엔비디아 급등에 반도체 강세'
    assert payload['articles'][1]['title'] == '엔비디아 강세에 반도체 섹터 동반 상승'
    assert cluster_repository.calls[0][0] == 'get_cluster_by_uid'
    assert cluster_repository.calls[1][0] == 'get_cluster_articles'
