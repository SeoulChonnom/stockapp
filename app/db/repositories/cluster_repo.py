from __future__ import annotations

from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings


def _qualified_table(table_name: str) -> str:
    return f'{get_settings().database_schema}.{table_name}'


class ClusterRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_cluster_by_uid(self, cluster_uid: str | UUID) -> dict | None:
        cluster_uid_value = (
            cluster_uid if isinstance(cluster_uid, UUID) else UUID(str(cluster_uid))
        )
        statement = text(
            """
            SELECT
                id,
                cluster_uid,
                business_date,
                market_type,
                cluster_rank,
                title,
                summary_short,
                summary_long,
                analysis_paragraphs_json,
                tags_json,
                representative_article_id,
                article_count,
                updated_at AS last_updated_at
            FROM {cluster_table}
            WHERE cluster_uid = :cluster_uid
            """.format(cluster_table=_qualified_table('news_cluster'))
        ).bindparams(bindparam('cluster_uid', cluster_uid_value))
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_cluster_articles(self, cluster_id: int) -> list[dict]:
        statement = text(
            """
            SELECT
                cluster_id,
                processed_article_id,
                article_rank
            FROM {cluster_article_table}
            WHERE cluster_id = :cluster_id
            ORDER BY article_rank ASC, processed_article_id ASC
            """.format(cluster_article_table=_qualified_table('news_cluster_article'))
        ).bindparams(bindparam('cluster_id', cluster_id))
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def list_clusters_by_business_date(
        self,
        business_date,
        *,
        market_type: str | None = None,
    ) -> list[dict]:
        where_clauses = ['c.business_date = :business_date']
        params: dict[str, object] = {'business_date': business_date}
        if market_type is not None:
            where_clauses.append(
                f'c.market_type = CAST(:market_type AS '
                f'{_qualified_table("market_type_enum")})'
            )
            params['market_type'] = market_type
        statement = text(
            """
            SELECT
                c.id,
                c.cluster_uid,
                c.business_date,
                c.market_type,
                c.cluster_rank,
                c.title,
                c.summary_short,
                c.summary_long,
                c.analysis_paragraphs_json,
                c.tags_json,
                c.representative_article_id,
                c.article_count,
                c.created_at,
                c.updated_at,
                p.canonical_title AS representative_title,
                p.publisher_name AS representative_publisher_name,
                p.published_at AS representative_published_at,
                p.origin_link AS representative_origin_link,
                p.naver_link AS representative_naver_link
            FROM {cluster_table} c
            LEFT JOIN {processed_article_table} p
              ON p.id = c.representative_article_id
            WHERE {where_sql}
            ORDER BY c.market_type ASC, c.cluster_rank ASC
            """.format(
                cluster_table=_qualified_table('news_cluster'),
                processed_article_table=_qualified_table('news_article_processed'),
                where_sql=' AND '.join(where_clauses),
            )
        )
        result = await self.session.execute(statement, params)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_processed_articles(self, article_ids: list[int]) -> list[dict]:
        if not article_ids:
            return []
        statement = text(
            """
            SELECT
                id,
                business_date,
                market_type,
                dedupe_hash,
                canonical_title,
                publisher_name,
                published_at,
                origin_link,
                naver_link,
                source_summary,
                article_body_excerpt,
                content_json
            FROM {processed_article_table}
            WHERE id IN :article_ids
            ORDER BY id ASC
            """.format(
                processed_article_table=_qualified_table('news_article_processed')
            )
        ).bindparams(bindparam('article_ids', article_ids, expanding=True))
        result = await self.session.execute(statement)
        rows = [self._row_to_dict(row) for row in result.all()]
        by_id = {row['id']: row for row in rows}
        return [by_id[article_id] for article_id in article_ids if article_id in by_id]

    async def list_cluster_article_links_by_business_date(
        self,
        business_date,
        *,
        market_type: str | None = None,
    ) -> list[dict]:
        where_clauses = ['c.business_date = :business_date']
        params: dict[str, object] = {'business_date': business_date}
        if market_type is not None:
            where_clauses.append(
                f'c.market_type = CAST(:market_type AS '
                f'{_qualified_table("market_type_enum")})'
            )
            params['market_type'] = market_type
        statement = text(
            """
            SELECT
                c.id AS cluster_id,
                c.cluster_uid,
                c.market_type,
                c.cluster_rank,
                c.title AS cluster_title,
                ca.processed_article_id,
                ca.article_rank,
                p.canonical_title AS title,
                p.publisher_name,
                p.published_at,
                p.origin_link,
                p.naver_link
            FROM {cluster_table} c
            JOIN {cluster_article_table} ca
              ON ca.cluster_id = c.id
            JOIN {processed_article_table} p
              ON p.id = ca.processed_article_id
            WHERE {where_sql}
            ORDER BY
                c.market_type ASC,
                c.cluster_rank ASC,
                ca.article_rank ASC,
                p.published_at DESC,
                p.id ASC
            """.format(
                cluster_table=_qualified_table('news_cluster'),
                cluster_article_table=_qualified_table('news_cluster_article'),
                processed_article_table=_qualified_table('news_article_processed'),
                where_sql=' AND '.join(where_clauses),
            )
        )
        result = await self.session.execute(statement, params)
        return [self._row_to_dict(row) for row in result.all()]

    @staticmethod
    def _row_to_dict(row: object) -> dict:
        mapping = getattr(row, '_mapping', None)
        if mapping is not None:
            return dict(mapping)
        return dict(row)  # type: ignore[arg-type]

    @staticmethod
    def _first_row(result: object) -> object | None:
        if hasattr(result, 'one_or_none'):
            return result.one_or_none()  # type: ignore[no-any-return]
        rows = result.all()  # type: ignore[no-any-return]
        return rows[0] if rows else None


__all__ = ['ClusterRepository']
