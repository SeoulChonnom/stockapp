from __future__ import annotations

from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository

_PAGE_HEADER_BASE_COLUMNS: tuple[str, ...] = (
    'id',
    'business_date',
    'version_no',
    'page_title',
    'status',
    'global_headline',
    'generated_at',
    'partial_message',
    'raw_news_count',
    'processed_news_count',
    'cluster_count',
    'last_updated_at',
    'metadata_json',
)


def _join_columns(columns: tuple[str, ...], indent: str) -> str:
    return f',\n{indent}'.join(columns)


_PAGE_HEADER_OUTER_COLUMNS_SQL = (
    f'{_join_columns(_PAGE_HEADER_BASE_COLUMNS, " " * 16)},\n                is_latest'
)
_PAGE_HEADER_LATEST_COLUMNS_SQL = _join_columns(
    (
        *_PAGE_HEADER_BASE_COLUMNS[:-1],
        'true as is_latest',
        _PAGE_HEADER_BASE_COLUMNS[-1],
    ),
    ' ' * 16,
)
_PAGE_HEADER_INNER_COLUMNS_SQL = (
    f'{_join_columns(_PAGE_HEADER_BASE_COLUMNS, " " * 20)},\n'
    '                    (version_no = max(version_no) '
    'over (partition by business_date)) as is_latest'
)

# Upper bound for the version picker payload. A business date realistically
# holds a handful of versions (one per batch rerun); the cap only exists so a
# pathological date cannot return an unbounded list on every page render.
PAGE_VERSION_LIST_LIMIT = 20
PUBLIC_PAGE_STATUSES: tuple[str, ...] = ('READY', 'PARTIAL')


def _public_page_status_predicate(column: str = 'status') -> str:
    statuses = ', '.join(f"'{status}'" for status in PUBLIC_PAGE_STATUSES)
    return f'{column} IN ({statuses})'


class PageSnapshotRepository(PostgresRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_latest_page_header(self) -> dict | None:
        statement = text(
            """
            select
                {columns}
            from {page_table}
            order by business_date desc, version_no desc
            limit 1
            """.format(
                columns=_PAGE_HEADER_LATEST_COLUMNS_SQL,
                page_table=qualify_db_identifier('market_daily_page'),
            )
        )
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_latest_public_page_header(self) -> dict | None:
        statement = text(
            """
            select
                {columns}
            from {page_table}
            where {public_status_predicate}
            order by business_date desc, version_no desc, id desc
            limit 1
            """.format(
                columns=_PAGE_HEADER_LATEST_COLUMNS_SQL,
                page_table=qualify_db_identifier('market_daily_page'),
                public_status_predicate=_public_page_status_predicate(),
            )
        )
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_page_header_by_business_date(
        self,
        business_date: date,
        version_no: int | None = None,
    ) -> dict | None:
        if version_no is None:
            statement = text(
                """
                select
                    {outer_columns}
                from (
                    select
                        {inner_columns}
                    from {page_table}
                    where business_date = :business_date
                        and {public_status_predicate}
                ) page_versions
                order by business_date desc, version_no desc, id desc
                limit 1
                """.format(
                    outer_columns=_PAGE_HEADER_OUTER_COLUMNS_SQL,
                    inner_columns=_PAGE_HEADER_INNER_COLUMNS_SQL,
                    page_table=qualify_db_identifier('market_daily_page'),
                    public_status_predicate=_public_page_status_predicate(),
                )
            ).bindparams(bindparam('business_date', business_date))
        else:
            statement = text(
                """
                select
                    {outer_columns}
                from (
                    select
                        {inner_columns}
                    from {page_table}
                    where business_date = :business_date
                ) page_versions
                where version_no = :version_no
                order by version_no desc
                limit 1
                """.format(
                    outer_columns=_PAGE_HEADER_OUTER_COLUMNS_SQL,
                    inner_columns=_PAGE_HEADER_INNER_COLUMNS_SQL,
                    page_table=qualify_db_identifier('market_daily_page'),
                )
            ).bindparams(
                bindparam('business_date', business_date),
                bindparam('version_no', version_no),
            )
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_page_header_by_id(self, page_id: int) -> dict | None:
        statement = text(
            """
            SELECT
                {outer_columns}
            FROM (
                SELECT
                    {inner_columns}
                FROM {page_table}
            ) page_versions
            WHERE id = :page_id
            """.format(
                outer_columns=_PAGE_HEADER_OUTER_COLUMNS_SQL,
                inner_columns=_PAGE_HEADER_INNER_COLUMNS_SQL,
                page_table=qualify_db_identifier('market_daily_page'),
            )
        ).bindparams(bindparam('page_id', page_id))
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def exists_page_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            select 1
            from {page_table}
            where business_date = :business_date
            limit 1
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        return self._first_row(result) is not None

    async def exists_public_page_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            select 1
            from {page_table}
            where business_date = :business_date
                and {public_status_predicate}
            limit 1
            """.format(
                page_table=qualify_db_identifier('market_daily_page'),
                public_status_predicate=_public_page_status_predicate(),
            )
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        return self._first_row(result) is not None

    async def get_latest_version_no(self, business_date: date) -> int | None:
        statement = text(
            """
            select max(version_no)
            from {page_table}
            where business_date = :business_date
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        value = result.scalar_one_or_none()
        return int(value) if value is not None else None

    async def get_adjacent_business_dates(
        self, business_date: date
    ) -> dict[str, date | None]:
        """Nearest existing business dates on either side of ``business_date``.

        Uses strict comparisons so the lookup is correct even when
        ``business_date`` itself has no page row, and resolves both neighbors
        in a single round trip.
        """
        statement = text(
            """
            select
                max(business_date) filter (
                    where business_date < :business_date
                ) as previous_business_date,
                min(business_date) filter (
                    where business_date > :business_date
                ) as next_business_date
            from {page_table}
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        row = self._first_row(result)
        if row is None:
            return {'previous_business_date': None, 'next_business_date': None}
        mapping = self._row_to_dict(row)
        return {
            'previous_business_date': mapping.get('previous_business_date'),
            'next_business_date': mapping.get('next_business_date'),
        }

    async def get_adjacent_public_business_dates(
        self, business_date: date
    ) -> dict[str, date | None]:
        statement = text(
            """
            select
                max(business_date) filter (
                    where business_date < :business_date
                ) as previous_business_date,
                min(business_date) filter (
                    where business_date > :business_date
                ) as next_business_date
            from {page_table}
            where {public_status_predicate}
            """.format(
                page_table=qualify_db_identifier('market_daily_page'),
                public_status_predicate=_public_page_status_predicate(),
            )
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        row = self._first_row(result)
        if row is None:
            return {'previous_business_date': None, 'next_business_date': None}
        mapping = self._row_to_dict(row)
        return {
            'previous_business_date': mapping.get('previous_business_date'),
            'next_business_date': mapping.get('next_business_date'),
        }

    async def list_page_versions(
        self,
        business_date: date,
        *,
        limit: int = PAGE_VERSION_LIST_LIMIT,
    ) -> list[dict]:
        statement = text(
            """
            select
                id,
                business_date,
                version_no,
                status,
                generated_at,
                (version_no = max(version_no) over (partition by business_date))
                    as is_latest
            from {page_table}
            where business_date = :business_date
            order by version_no desc
            limit :limit
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(
            bindparam('business_date', business_date),
            bindparam('limit', limit),
        )
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_page_markets(self, page_id: int) -> list[dict]:
        statement = text(
            """
            SELECT
                id,
                page_id,
                market_type,
                expected_session_date,
                actual_index_source_date,
                session_close_at,
                news_window_start_at,
                news_window_end_at,
                news_coverage_complete,
                display_order,
                market_label,
                summary_title,
                summary_body,
                analysis_background_json,
                analysis_key_themes_json,
                analysis_outlook,
                search_document,
                raw_news_count,
                processed_news_count,
                cluster_count,
                last_updated_at,
                partial_message,
                metadata_json
            FROM {page_market_table}
            WHERE page_id = :page_id
            ORDER BY display_order
            """.format(
                page_market_table=qualify_db_identifier('market_daily_page_market')
            )
        ).bindparams(bindparam('page_id', page_id))
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_page_indices(self, page_market_ids: list[int]) -> list[dict]:
        if not page_market_ids:
            return []
        statement = text(
            """
            SELECT
                id,
                page_market_id,
                market_index_daily_id,
                source_date,
                expected_session_date,
                session_close_at,
                display_order,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                high_price,
                low_price,
                currency_code
            FROM {page_market_index_table}
            WHERE page_market_id IN :page_market_ids
            ORDER BY page_market_id, display_order
            """.format(
                page_market_index_table=qualify_db_identifier(
                    'market_daily_page_market_index'
                )
            )
        ).bindparams(bindparam('page_market_ids', page_market_ids, expanding=True))
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_page_clusters(self, page_market_ids: list[int]) -> list[dict]:
        if not page_market_ids:
            return []
        statement = text(
            """
            SELECT
                id,
                page_market_id,
                cluster_id,
                cluster_uid,
                display_order,
                title,
                summary,
                search_document,
                article_count,
                tags_json,
                representative_article_id,
                representative_title,
                representative_publisher_name,
                representative_published_at,
                representative_origin_link,
                representative_naver_link
            FROM {page_market_cluster_table}
            WHERE page_market_id IN :page_market_ids
            ORDER BY page_market_id, display_order
            """.format(
                page_market_cluster_table=qualify_db_identifier(
                    'market_daily_page_market_cluster'
                )
            )
        ).bindparams(bindparam('page_market_ids', page_market_ids, expanding=True))
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_page_cluster_themes(
        self, page_market_cluster_ids: list[int]
    ) -> list[dict]:
        """Return ranked theme rows stored with page-cluster snapshots."""
        if not page_market_cluster_ids:
            return []
        statement = text(
            """
            SELECT
                page_market_cluster_id,
                theme_code,
                rank
            FROM {theme_table}
            WHERE page_market_cluster_id IN :page_market_cluster_ids
            ORDER BY page_market_cluster_id, rank
            """.format(
                theme_table=qualify_db_identifier(
                    'market_daily_page_market_cluster_theme'
                )
            )
        ).bindparams(
            bindparam(
                'page_market_cluster_ids',
                page_market_cluster_ids,
                expanding=True,
            )
        )
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def get_page_article_links(self, page_market_ids: list[int]) -> list[dict]:
        if not page_market_ids:
            return []
        statement = text(
            """
            SELECT
                id,
                page_market_id,
                display_order,
                processed_article_id,
                cluster_id,
                cluster_uid,
                cluster_title,
                title,
                publisher_name,
                published_at,
                origin_link,
                naver_link
            FROM {page_article_link_table}
            WHERE page_market_id IN :page_market_ids
              AND processed_article_id IS NOT NULL
              AND cluster_uid IS NOT NULL
            ORDER BY page_market_id, display_order
            """.format(
                page_article_link_table=qualify_db_identifier(
                    'market_daily_page_article_link'
                )
            )
        ).bindparams(bindparam('page_market_ids', page_market_ids, expanding=True))
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def list_archive_page_headers(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 30,
    ) -> list[dict]:
        filters = self._build_filters(
            from_date=from_date, to_date=to_date, status=status
        )
        statement = text(
            """
            WITH latest_public AS (
                SELECT DISTINCT ON (business_date)
                    id,
                    business_date,
                    version_no,
                    page_title,
                    status,
                    global_headline,
                    generated_at,
                    partial_message
                FROM {page_table}
                WHERE {public_status_predicate}
                ORDER BY business_date DESC, version_no DESC, id DESC
            )
            SELECT
                id AS "pageId",
                business_date AS "businessDate",
                page_title AS "pageTitle",
                global_headline AS "headlineSummary",
                status,
                generated_at AS "generatedAt",
                partial_message AS "partialMessage"
            FROM latest_public
            {where_clause}
            ORDER BY business_date DESC
            LIMIT :limit OFFSET :offset
            """.format(
                page_table=qualify_db_identifier('market_daily_page'),
                public_status_predicate=_public_page_status_predicate(),
                where_clause=filters['where'],
            )
        ).bindparams(
            *filters['bindparams'],
            bindparam('limit', size),
            bindparam('offset', (page - 1) * size),
        )
        result = await self.session.execute(statement)
        return [self._row_to_dict(row) for row in result.all()]

    async def count_archive_page_headers(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        status: str | None = None,
    ) -> int:
        filters = self._build_filters(
            from_date=from_date, to_date=to_date, status=status
        )
        statement = text(
            """
            WITH latest_public AS (
                SELECT DISTINCT ON (business_date)
                    id,
                    business_date,
                    version_no,
                    page_title,
                    status,
                    global_headline,
                    generated_at,
                    partial_message
                FROM {page_table}
                WHERE {public_status_predicate}
                ORDER BY business_date DESC, version_no DESC, id DESC
            )
            SELECT COUNT(*)
            FROM latest_public
            {where_clause}
            """.format(
                page_table=qualify_db_identifier('market_daily_page'),
                public_status_predicate=_public_page_status_predicate(),
                where_clause=filters['where'],
            )
        ).bindparams(*filters['bindparams'])
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    @staticmethod
    def _build_filters(
        *,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
    ) -> dict:
        clauses: list[str] = []
        params = []
        if from_date is not None:
            clauses.append('business_date >= :from_date')
            params.append(bindparam('from_date', from_date))
        if to_date is not None:
            clauses.append('business_date <= :to_date')
            params.append(bindparam('to_date', to_date))
        if status is not None:
            clauses.append(
                f'status = CAST(UPPER(:status) AS {qualify_db_identifier("page_status_enum")})'
            )
            params.append(bindparam('status', status))
        where = f'WHERE {" AND ".join(clauses)}' if clauses else ''
        return {'where': where, 'bindparams': params}


__all__ = [
    'PAGE_VERSION_LIST_LIMIT',
    'PUBLIC_PAGE_STATUSES',
    'PageSnapshotRepository',
]
