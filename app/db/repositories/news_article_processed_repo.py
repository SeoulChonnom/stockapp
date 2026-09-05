from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsArticleProcessedCreateParams,
    NewsArticleProcessedRecord,
    NewsArticleRawProcessedMapCreateParams,
)


class NewsArticleProcessedRepository(PostgresRepository):
    async def get_processed_by_dedupe_hash(
        self,
        business_date: date,
        market_type: str,
        dedupe_hash: str,
    ) -> NewsArticleProcessedRecord | None:
        statement = text(
            """
            SELECT
                id AS processed_article_id,
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
                content_json,
                created_at,
                updated_at
            FROM {processed_table}
            WHERE business_date = :business_date
              AND market_type = CAST(:market_type AS {market_type_enum})
              AND dedupe_hash = :dedupe_hash
            """.format(
                processed_table=qualify_db_identifier('news_article_processed'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': business_date,
                'market_type': market_type,
                'dedupe_hash': dedupe_hash,
            },
        )
        row = result.mappings().one_or_none()
        return (
            self._model_from_mapping(NewsArticleProcessedRecord, row) if row else None
        )

    async def list_processed_by_business_date(
        self,
        business_date: date,
        *,
        market_type: str | None = None,
        limit: int | None = None,
    ) -> list[NewsArticleProcessedRecord]:
        where_clauses = ['business_date = :business_date']
        params: dict[str, object] = {'business_date': business_date}
        if market_type is not None:
            where_clauses.append(
                f'market_type = CAST(:market_type AS '
                f'{qualify_db_identifier("market_type_enum")})'
            )
            params['market_type'] = market_type
        where_sql = ' AND '.join(where_clauses)

        if limit is None:
            return await self._list_processed_unranked(where_sql, params)
        params['limit'] = limit
        return await self._list_processed_ranked_per_market(where_sql, params)

    async def _list_processed_unranked(
        self, where_sql: str, params: dict[str, object]
    ) -> list[NewsArticleProcessedRecord]:
        statement = text(
            """
            SELECT
                id AS processed_article_id,
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
                content_json,
                created_at,
                updated_at
            FROM {processed_table}
            WHERE {where_sql}
            ORDER BY market_type ASC, published_at DESC NULLS LAST, id ASC
            """.format(
                processed_table=qualify_db_identifier('news_article_processed'),
                where_sql=where_sql,
            )
        )
        result = await self.session.execute(statement, params)
        return self._models_from_mappings(
            NewsArticleProcessedRecord, result.mappings().all()
        )

    async def _list_processed_ranked_per_market(
        self, where_sql: str, params: dict[str, object]
    ) -> list[NewsArticleProcessedRecord]:
        """Apply the caller's LIMIT within each market_type partition.

        market_type_enum orders US before KR, so a plain global LIMIT spends
        its whole budget on whichever market sorts first and can starve the
        other market's clustering input completely on a busy day (see
        BuildClustersStep). Ranking rows within each market before capping
        turns the limit into a per-market allocation instead of one shared,
        order-dependent budget.
        """
        statement = text(
            """
            WITH ranked AS (
                SELECT
                    id AS processed_article_id,
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
                    content_json,
                    created_at,
                    updated_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY market_type
                        ORDER BY published_at DESC NULLS LAST, id ASC
                    ) AS market_rank
                FROM {processed_table}
                WHERE {where_sql}
            )
            SELECT
                processed_article_id,
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
                content_json,
                created_at,
                updated_at
            FROM ranked
            WHERE market_rank <= :limit
            ORDER BY market_type ASC, published_at DESC NULLS LAST, id ASC
            """.format(
                processed_table=qualify_db_identifier('news_article_processed'),
                where_sql=where_sql,
            )
        )
        result = await self.session.execute(statement, params)
        return self._models_from_mappings(
            NewsArticleProcessedRecord, result.mappings().all()
        )

    async def list_by_business_date(
        self,
        business_date: date,
        *,
        market_type: str | None = None,
        limit: int | None = None,
    ) -> list[NewsArticleProcessedRecord]:
        return await self.list_processed_by_business_date(
            business_date, market_type=market_type, limit=limit
        )

    async def count_processed_by_business_date(
        self, business_date: date
    ) -> dict[str, int]:
        """Return each market's total processed-article count for a date.

        `list_processed_by_business_date` only returns the rows that survive
        its LIMIT, so it cannot say how many rows a market actually had.
        Callers that need to report an accurate per-market omission count
        (e.g. BuildClustersStep's clustering-limit WARN) call this alongside
        it to learn the true totals.
        """
        statement = text(
            """
            SELECT market_type, COUNT(*) AS article_count
            FROM {processed_table}
            WHERE business_date = :business_date
            GROUP BY market_type
            """.format(processed_table=qualify_db_identifier('news_article_processed'))
        )
        result = await self.session.execute(statement, {'business_date': business_date})
        return {
            row['market_type']: row['article_count'] for row in result.mappings().all()
        }

    async def insert_processed_article(
        self,
        params: NewsArticleProcessedCreateParams,
    ) -> NewsArticleProcessedRecord:
        statement = text(
            """
            INSERT INTO {processed_table} (
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
            )
            VALUES (
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :dedupe_hash,
                :canonical_title,
                :publisher_name,
                :published_at,
                :origin_link,
                :naver_link,
                :source_summary,
                :article_body_excerpt,
                CAST(:content_json AS JSONB)
            )
            ON CONFLICT (business_date, market_type, dedupe_hash) DO NOTHING
            RETURNING
                id AS processed_article_id,
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
                content_json,
                created_at,
                updated_at
            """.format(
                processed_table=qualify_db_identifier('news_article_processed'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': params.business_date,
                'market_type': params.market_type,
                'dedupe_hash': params.dedupe_hash,
                'canonical_title': params.canonical_title,
                'publisher_name': params.publisher_name,
                'published_at': params.published_at,
                'origin_link': params.origin_link,
                'naver_link': params.naver_link,
                'source_summary': params.source_summary,
                'article_body_excerpt': params.article_body_excerpt,
                'content_json': json.dumps(params.content_json),
            },
        )
        inserted = result.mappings().one_or_none()
        if inserted is not None:
            return self._model_from_mapping(NewsArticleProcessedRecord, inserted)

        existing = await self.get_processed_by_dedupe_hash(
            params.business_date,
            params.market_type,
            params.dedupe_hash,
        )
        if existing is None:
            raise RuntimeError('Processed article upsert failed unexpectedly.')
        return existing

    async def insert_raw_processed_map(
        self, params: NewsArticleRawProcessedMapCreateParams
    ) -> None:
        statement = text(
            """
            INSERT INTO {mapping_table} (
                raw_article_id,
                processed_article_id
            )
            VALUES (
                :raw_article_id,
                :processed_article_id
            )
            ON CONFLICT (raw_article_id, processed_article_id) DO NOTHING
            """.format(
                mapping_table=qualify_db_identifier('news_article_raw_processed_map')
            )
        )
        await self.session.execute(
            statement,
            {
                'raw_article_id': params.raw_article_id,
                'processed_article_id': params.processed_article_id,
            },
        )

    async def get_or_create_processed_article(
        self,
        params: NewsArticleProcessedCreateParams,
    ) -> NewsArticleProcessedRecord:
        return await self.insert_processed_article(params)

    async def link_raw_to_processed(
        self, params: NewsArticleRawProcessedMapCreateParams
    ) -> None:
        await self.insert_raw_processed_map(params)


__all__ = ['NewsArticleProcessedRepository']
