from __future__ import annotations

from typing import Any

from app.core.exceptions import NotFoundError
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.cluster_repo import ClusterRepository
from app.domains.clusters.assembler import build_cluster_detail_payload


class ClustersService:
    def __init__(
        self,
        repository: ClusterRepository,
        ai_summary_repository: AiSummaryRepository | None = None,
    ) -> None:
        self._repo = repository
        self._ai_summary_repo = ai_summary_repository

    async def get_cluster_detail(self, cluster_id: str) -> dict[str, Any]:
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
        missing_article_ids = [
            article_id for article_id in article_ids if article_id not in by_id
        ]
        if missing_article_ids:
            raise ValueError(
                f'cluster {cluster["id"]} references missing processed '
                f'article ids: {missing_article_ids}'
            )
        ordered_articles = [
            by_id[row['processed_article_id']] for row in cluster_articles
        ]
        grouping = await self._repo.get_cluster_grouping(cluster['id'])
        ai_summary = None
        if self._ai_summary_repo is not None:
            ai_summary = await self._ai_summary_repo.get_latest_cluster_summary(
                cluster['id'],
                summary_type='CLUSTER_DETAIL_ANALYSIS',
            )
        return build_cluster_detail_payload(
            cluster,
            representative_article,
            ordered_articles,
            ai_summary,
            grouping,
        )


__all__ = ['ClustersService']
