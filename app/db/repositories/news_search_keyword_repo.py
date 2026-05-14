from __future__ import annotations

from sqlalchemy import bindparam, text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsSearchKeywordCreateParams,
    NewsSearchKeywordRecord,
    NewsSearchKeywordUpdateParams,
)


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


class NewsSearchKeywordRepository(PostgresRepository):
    async def get_keyword_by_id(
        self, keyword_id: int
    ) -> NewsSearchKeywordRecord | None:
        statement = text(
            """
            SELECT
                id AS keyword_id,
                provider_name,
                market_type,
                keyword,
                is_active,
                priority,
                created_at,
                updated_at
            FROM {keyword_table}
            WHERE id = :keyword_id
            """.format(keyword_table=_qualified_table('news_search_keyword'))
        ).bindparams(bindparam('keyword_id', keyword_id))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(NewsSearchKeywordRecord, row) if row else None

    async def list_keywords(
        self,
        *,
        provider_name: str | None = None,
        market_type: str | None = None,
        is_active: bool | None = None,
    ) -> list[NewsSearchKeywordRecord]:
        where_clauses: list[str] = []
        params: dict[str, object] = {}

        if provider_name is not None:
            where_clauses.append('provider_name = :provider_name')
            params['provider_name'] = provider_name
        if market_type is not None:
            where_clauses.append(
                f'market_type = CAST(:market_type AS '
                f'{_qualified_table("market_type_enum")})'
            )
            params['market_type'] = market_type
        if is_active is not None:
            where_clauses.append('is_active = :is_active')
            params['is_active'] = is_active

        where_sql = ''
        if where_clauses:
            where_sql = 'WHERE ' + ' AND '.join(where_clauses)

        statement = text(
            """
            SELECT
                id AS keyword_id,
                provider_name,
                market_type,
                keyword,
                is_active,
                priority,
                created_at,
                updated_at
            FROM {keyword_table}
            {where_sql}
            ORDER BY provider_name ASC, market_type ASC, priority ASC, id ASC
            """.format(
                keyword_table=_qualified_table('news_search_keyword'),
                where_sql=where_sql,
            )
        )
        result = await self.session.execute(statement, params)
        return self._models_from_mappings(
            NewsSearchKeywordRecord, result.mappings().all()
        )

    async def list_active_keywords(
        self,
        *,
        provider_name: str,
        market_type: str | None = None,
    ) -> list[NewsSearchKeywordRecord]:
        return await self.list_keywords(
            provider_name=provider_name,
            market_type=market_type,
            is_active=True,
        )

    async def create_keyword(
        self,
        params: NewsSearchKeywordCreateParams,
    ) -> NewsSearchKeywordRecord:
        statement = text(
            """
            INSERT INTO {keyword_table} (
                provider_name,
                market_type,
                keyword,
                is_active,
                priority
            )
            VALUES (
                :provider_name,
                CAST(:market_type AS {market_type_enum}),
                :keyword,
                :is_active,
                :priority
            )
            RETURNING
                id AS keyword_id,
                provider_name,
                market_type,
                keyword,
                is_active,
                priority,
                created_at,
                updated_at
            """.format(
                keyword_table=_qualified_table('news_search_keyword'),
                market_type_enum=_qualified_table('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'provider_name': params.provider_name,
                'market_type': params.market_type,
                'keyword': params.keyword,
                'is_active': params.is_active,
                'priority': params.priority,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(NewsSearchKeywordRecord, row)

    async def update_keyword(
        self,
        *,
        keyword_id: int,
        params: NewsSearchKeywordUpdateParams,
    ) -> NewsSearchKeywordRecord | None:
        statement = text(
            """
            UPDATE {keyword_table}
            SET
                keyword = COALESCE(:keyword, keyword),
                priority = COALESCE(:priority, priority),
                is_active = COALESCE(:is_active, is_active),
                updated_at = now()
            WHERE id = :keyword_id
            RETURNING
                id AS keyword_id,
                provider_name,
                market_type,
                keyword,
                is_active,
                priority,
                created_at,
                updated_at
            """.format(keyword_table=_qualified_table('news_search_keyword'))
        )
        result = await self.session.execute(
            statement,
            {
                'keyword_id': keyword_id,
                'keyword': params.keyword,
                'priority': params.priority,
                'is_active': params.is_active,
            },
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(NewsSearchKeywordRecord, row) if row else None


__all__ = ['NewsSearchKeywordRepository']
