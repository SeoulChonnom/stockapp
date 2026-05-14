from __future__ import annotations

import json
from datetime import date

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsArticleRawCreateParams,
    NewsArticleRawRecord,
)


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


class NewsArticleRawRepository(PostgresRepository):
    async def list_articles_by_business_date(
        self,
        business_date: date,
        *,
        market_type: str | None = None,
    ) -> list[NewsArticleRawRecord]:
        where_clauses = ['business_date = :business_date']
        params: dict[str, object] = {'business_date': business_date}
        if market_type is not None:
            where_clauses.append(
                f'market_type = CAST(:market_type AS '
                f'{_qualified_table("market_type_enum")})'
            )
            params['market_type'] = market_type

        statement = text(
            """
            SELECT
                id AS raw_article_id,
                provider_name,
                provider_article_key,
                market_type,
                business_date,
                search_keyword,
                title,
                publisher_name,
                published_at,
                origin_link,
                naver_link,
                payload_json,
                collected_at,
                created_at
            FROM {raw_table}
            WHERE {where_sql}
            ORDER BY market_type ASC, published_at DESC NULLS LAST, id ASC
            """.format(
                raw_table=_qualified_table('news_article_raw'),
                where_sql=' AND '.join(where_clauses),
            )
        )
        result = await self.session.execute(statement, params)
        return self._models_from_mappings(NewsArticleRawRecord, result.mappings().all())

    async def insert_articles(self, articles: list[NewsArticleRawCreateParams]) -> int:
        if not articles:
            return 0

        statement = text(
            """
            INSERT INTO {raw_table} (
                provider_name,
                provider_article_key,
                market_type,
                business_date,
                search_keyword,
                title,
                publisher_name,
                published_at,
                origin_link,
                naver_link,
                payload_json
            )
            VALUES (
                :provider_name,
                :provider_article_key,
                CAST(:market_type AS {market_type_enum}),
                :business_date,
                :search_keyword,
                :title,
                :publisher_name,
                :published_at,
                :origin_link,
                :naver_link,
                CAST(:payload_json AS JSONB)
            )
            ON CONFLICT (provider_name, provider_article_key) DO NOTHING
            RETURNING id
            """.format(
                raw_table=_qualified_table('news_article_raw'),
                market_type_enum=_qualified_table('market_type_enum'),
            )
        )

        inserted_count = 0
        for article in articles:
            result = await self.session.execute(
                statement,
                {
                    'provider_name': article.provider_name,
                    'provider_article_key': article.provider_article_key,
                    'market_type': article.market_type,
                    'business_date': article.business_date,
                    'search_keyword': article.search_keyword,
                    'title': article.title,
                    'publisher_name': article.publisher_name,
                    'published_at': article.published_at,
                    'origin_link': article.origin_link,
                    'naver_link': article.naver_link,
                    'payload_json': json.dumps(article.payload_json),
                },
            )
            if result.scalar_one_or_none() is not None:
                inserted_count += 1

        await self.session.commit()
        return inserted_count


__all__ = ['NewsArticleRawRepository']
