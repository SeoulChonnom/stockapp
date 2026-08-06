from __future__ import annotations

import json
from datetime import date, datetime

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsArticleRawCreateParams,
    NewsArticleRawRecord,
)


class NewsArticleRawRepository(PostgresRepository):
    async def count_articles_by_business_date(self, business_date: date) -> int:
        statement = text(
            """
            SELECT COUNT(*)
            FROM {raw_table}
            WHERE business_date = :business_date
            """.format(raw_table=qualify_db_identifier('news_article_raw'))
        )
        result = await self.session.execute(
            statement,
            {'business_date': business_date},
        )
        return int(result.scalar_one())

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
                f'{qualify_db_identifier("market_type_enum")})'
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
                raw_table=qualify_db_identifier('news_article_raw'),
                where_sql=' AND '.join(where_clauses),
            )
        )
        result = await self.session.execute(statement, params)
        return self._models_from_mappings(NewsArticleRawRecord, result.mappings().all())

    async def list_articles_by_window(
        self,
        *,
        window_start_at: datetime,
        window_end_at: datetime,
        market_type: str,
    ) -> list[NewsArticleRawRecord]:
        statement = text(
            """
            SELECT
                id AS raw_article_id,
                provider_name,
                provider_article_key,
                CAST(:market_type AS {market_type_enum}) AS market_type,
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
            WHERE (
                  market_type = CAST(:market_type AS {market_type_enum})
                  OR EXISTS (
                      SELECT 1
                      FROM {keyword_match_table} keyword_match
                      WHERE keyword_match.raw_article_id = {raw_table}.id
                        AND keyword_match.market_type = CAST(
                            :market_type AS {market_type_enum}
                        )
                  )
              )
              AND published_at >= :window_start_at
              AND published_at < :window_end_at
            ORDER BY published_at DESC, id ASC
            """.format(
                raw_table=qualify_db_identifier('news_article_raw'),
                keyword_match_table=qualify_db_identifier(
                    'news_article_raw_keyword_match'
                ),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'market_type': market_type,
                'window_start_at': window_start_at,
                'window_end_at': window_end_at,
            },
        )
        return self._models_from_mappings(
            NewsArticleRawRecord,
            result.mappings().all(),
        )

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
            ON CONFLICT (provider_name, provider_article_key)
            DO NOTHING
            RETURNING id
            """.format(
                raw_table=qualify_db_identifier('news_article_raw'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
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

        return inserted_count

    async def link_articles_to_keyword(
        self,
        *,
        articles: list[NewsArticleRawCreateParams],
        keyword_id: int,
        market_type: str,
    ) -> None:
        if not articles:
            return
        statement = text(
            """
            INSERT INTO {match_table} (
                raw_article_id,
                keyword_id,
                market_type
            )
            SELECT
                raw.id,
                :keyword_id,
                CAST(:market_type AS {market_type_enum})
            FROM {raw_table} raw
            WHERE raw.provider_name = :provider_name
              AND raw.provider_article_key = :provider_article_key
            ON CONFLICT (raw_article_id, keyword_id) DO NOTHING
            """.format(
                match_table=qualify_db_identifier('news_article_raw_keyword_match'),
                raw_table=qualify_db_identifier('news_article_raw'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        for article in articles:
            await self.session.execute(
                statement,
                {
                    'keyword_id': keyword_id,
                    'market_type': market_type,
                    'provider_name': article.provider_name,
                    'provider_article_key': article.provider_article_key,
                },
            )


__all__ = ['NewsArticleRawRepository']
