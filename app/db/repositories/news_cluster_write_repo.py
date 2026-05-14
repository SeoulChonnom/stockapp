from __future__ import annotations

import json
from datetime import date

from sqlalchemy import bindparam, text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsClusterArticleCreateParams,
    NewsClusterCreateParams,
    NewsClusterWriteRecord,
)


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


class NewsClusterWriteRepository(PostgresRepository):
    async def list_cluster_ids_for_business_date(
        self,
        business_date: date,
        market_type: str,
    ) -> list[int]:
        statement = text(
            """
            SELECT id
            FROM {cluster_table}
            WHERE business_date = :business_date
              AND market_type = CAST(:market_type AS {market_type_enum})
            ORDER BY cluster_rank ASC
            """.format(
                cluster_table=_qualified_table('news_cluster'),
                market_type_enum=_qualified_table('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {'business_date': business_date, 'market_type': market_type},
        )
        return list(result.scalars().all())

    async def delete_clusters_by_ids(self, cluster_ids: list[int]) -> None:
        if not cluster_ids:
            return
        statement = text(
            """
            DELETE FROM {cluster_table}
            WHERE id IN :cluster_ids
            """.format(cluster_table=_qualified_table('news_cluster'))
        ).bindparams(bindparam('cluster_ids', expanding=True))
        await self.session.execute(statement, {'cluster_ids': tuple(cluster_ids)})

    async def upsert_cluster(
        self, params: NewsClusterCreateParams
    ) -> NewsClusterWriteRecord:
        statement = text(
            """
            INSERT INTO {cluster_table} (
                business_date,
                market_type,
                cluster_rank,
                title,
                summary_short,
                summary_long,
                analysis_paragraphs_json,
                tags_json,
                representative_article_id,
                article_count
            )
            VALUES (
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :cluster_rank,
                :title,
                :summary_short,
                :summary_long,
                CAST(:analysis_paragraphs_json AS JSONB),
                CAST(:tags_json AS JSONB),
                :representative_article_id,
                :article_count
            )
            ON CONFLICT (business_date, market_type, cluster_rank) DO UPDATE
            SET
                title = EXCLUDED.title,
                summary_short = EXCLUDED.summary_short,
                summary_long = EXCLUDED.summary_long,
                analysis_paragraphs_json = EXCLUDED.analysis_paragraphs_json,
                tags_json = EXCLUDED.tags_json,
                representative_article_id = EXCLUDED.representative_article_id,
                article_count = EXCLUDED.article_count,
                updated_at = now()
            RETURNING
                id AS cluster_id,
                cluster_uid,
                cluster_rank
            """.format(
                cluster_table=_qualified_table('news_cluster'),
                market_type_enum=_qualified_table('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': params.business_date,
                'market_type': params.market_type,
                'cluster_rank': params.cluster_rank,
                'title': params.title,
                'summary_short': params.summary_short,
                'summary_long': params.summary_long,
                'analysis_paragraphs_json': json.dumps(params.analysis_paragraphs_json),
                'tags_json': json.dumps(params.tags_json),
                'representative_article_id': params.representative_article_id,
                'article_count': params.article_count,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(NewsClusterWriteRecord, row)

    async def replace_cluster_articles(
        self,
        cluster_id: int,
        memberships: list[NewsClusterArticleCreateParams],
    ) -> None:
        delete_statement = text(
            """
            DELETE FROM {cluster_article_table}
            WHERE cluster_id = :cluster_id
            """.format(cluster_article_table=_qualified_table('news_cluster_article'))
        )
        await self.session.execute(delete_statement, {'cluster_id': cluster_id})

        if memberships:
            insert_statement = text(
                """
                INSERT INTO {cluster_article_table} (
                    cluster_id,
                    processed_article_id,
                    article_rank
                )
                VALUES (
                    :cluster_id,
                    :processed_article_id,
                    :article_rank
                )
                """.format(
                    cluster_article_table=_qualified_table('news_cluster_article')
                )
            )
            for membership in memberships:
                await self.session.execute(
                    insert_statement,
                    {
                        'cluster_id': cluster_id,
                        'processed_article_id': membership.processed_article_id,
                        'article_rank': membership.article_rank,
                    },
                )

    async def create_cluster_bundle(
        self,
        params: NewsClusterCreateParams,
        article_ids: list[int],
    ) -> NewsClusterWriteRecord:
        cluster = await self.upsert_cluster(params)
        await self.replace_cluster_articles(
            cluster.cluster_id,
            [
                NewsClusterArticleCreateParams(
                    cluster_id=cluster.cluster_id,
                    processed_article_id=processed_article_id,
                    article_rank=index,
                )
                for index, processed_article_id in enumerate(article_ids, start=1)
            ],
        )
        return cluster


__all__ = ['NewsClusterWriteRepository']
