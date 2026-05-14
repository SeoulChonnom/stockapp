from __future__ import annotations

from app.api.deps import DbSession
from app.core.exceptions import NotFoundError
from app.db.repositories.cluster_repo import ClusterRepository
from app.domains.clusters.assembler import build_cluster_detail_response
from app.schemas.cluster import ClusterDetailResponse


class ClustersService:
    def __init__(self, repository: ClusterRepository) -> None:
        self._repo = repository

    async def get_cluster_detail(self, cluster_id: str) -> ClusterDetailResponse:
        cluster = await self._repo.get_cluster_by_uid(cluster_id)
        if cluster is None:
            raise NotFoundError(
                'CLUSTER_NOT_FOUND', '요청한 뉴스 클러스터를 찾을 수 없습니다.'
            )

        cluster_articles = await self._repo.get_cluster_articles(cluster['id'])
        article_ids = [row['processed_article_id'] for row in cluster_articles]
        processed_articles = await self._repo.get_processed_articles(article_ids)
        by_id = {row['id']: row for row in processed_articles}
        representative_article = by_id.get(cluster['representative_article_id'])
        if representative_article is None:
            raise NotFoundError(
                'CLUSTER_REPRESENTATIVE_ARTICLE_NOT_FOUND',
                '클러스터 대표 기사를 찾을 수 없습니다.',
            )
        ordered_articles = [
            by_id[row['processed_article_id']]
            for row in cluster_articles
            if row['processed_article_id'] in by_id
        ]
        return build_cluster_detail_response(
            cluster, representative_article, ordered_articles
        )


def get_clusters_service(session: DbSession) -> ClustersService:
    return ClustersService(ClusterRepository(session))


__all__ = ['ClustersService', 'get_clusters_service']
