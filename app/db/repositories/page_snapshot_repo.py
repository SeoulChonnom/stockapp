from __future__ import annotations

from datetime import date

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings


def _qualified_table(table_name: str) -> str:
    return f"{get_settings().database_schema}.{table_name}"


class PageSnapshotRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_latest_page_header(self) -> dict | None:
        statement = text(
            """
            select
                id,
                business_date,
                version_no,
                page_title,
                status,
                global_headline,
                generated_at,
                partial_message,
                raw_news_count,
                processed_news_count,
                cluster_count,
                last_updated_at,
                metadata_json
            from {page_table}
            order by business_date desc, version_no desc
            limit 1
            """
            .format(page_table=_qualified_table("market_daily_page"))
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
                    id,
                    business_date,
                    version_no,
                    page_title,
                    status,
                    global_headline,
                    generated_at,
                    partial_message,
                    raw_news_count,
                    processed_news_count,
                    cluster_count,
                    last_updated_at,
                    metadata_json
                from {page_table}
                where business_date = :business_date
                order by version_no desc
                limit 1
                """
                .format(page_table=_qualified_table("market_daily_page"))
            ).bindparams(
                bindparam("business_date", business_date)
            )
        else:
            statement = text(
                """
                select
                    id,
                    business_date,
                    version_no,
                    page_title,
                    status,
                    global_headline,
                    generated_at,
                    partial_message,
                    raw_news_count,
                    processed_news_count,
                    cluster_count,
                    last_updated_at,
                    metadata_json
                from {page_table}
                where business_date = :business_date
                  and version_no = :version_no
                order by version_no desc
                limit 1
                """
                .format(page_table=_qualified_table("market_daily_page"))
            ).bindparams(
                bindparam("business_date", business_date),
                bindparam("version_no", version_no),
            )
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_page_header_by_id(self, page_id: int) -> dict | None:
        statement = text(
            """
            SELECT
                id,
                business_date,
                version_no,
                page_title,
                status,
                global_headline,
                generated_at,
                partial_message,
                raw_news_count,
                processed_news_count,
                cluster_count,
                last_updated_at,
                metadata_json
            FROM {page_table}
            WHERE id = :page_id
            """
            .format(page_table=_qualified_table("market_daily_page"))
        ).bindparams(
            bindparam("page_id", page_id)
        )
        result = await self.session.execute(statement)
        row = self._first_row(result)
        return self._row_to_dict(row) if row else None

    async def get_page_markets(self, page_id: int) -> list[dict]:
        statement = text(
            """
            SELECT
                id,
                page_id,
                market_type,
                display_order,
                market_label,
                summary_title,
                summary_body,
                analysis_background_json,
                analysis_key_themes_json,
                analysis_outlook,
                raw_news_count,
                processed_news_count,
                cluster_count,
                last_updated_at,
                partial_message,
                metadata_json
            FROM {page_market_table}
            WHERE page_id = :page_id
            ORDER BY display_order
            """
            .format(page_market_table=_qualified_table("market_daily_page_market"))
        ).bindparams(
            bindparam("page_id", page_id)
        )
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
            """
            .format(
                page_market_index_table=_qualified_table("market_daily_page_market_index")
            )
        ).bindparams(bindparam("page_market_ids", page_market_ids, expanding=True))
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
            """
            .format(
                page_market_cluster_table=_qualified_table("market_daily_page_market_cluster")
            )
        ).bindparams(bindparam("page_market_ids", page_market_ids, expanding=True))
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
            ORDER BY page_market_id, display_order
            """
            .format(
                page_article_link_table=_qualified_table("market_daily_page_article_link")
            )
        ).bindparams(bindparam("page_market_ids", page_market_ids, expanding=True))
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
            SELECT DISTINCT ON (business_date)
                id AS "pageId",
                business_date AS "businessDate",
                page_title AS "pageTitle",
                global_headline AS "headlineSummary",
                status,
                generated_at AS "generatedAt",
                partial_message AS "partialMessage"
            FROM {page_table}
            {where_clause}
            ORDER BY business_date DESC, version_no DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
            .format(
                page_table=_qualified_table("market_daily_page"),
                where_clause=filters["where"],
            )
        ).bindparams(
            *filters["bindparams"],
            bindparam("limit", size),
            bindparam("offset", (page - 1) * size),
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
            SELECT COUNT(*)
            FROM (
                SELECT DISTINCT ON (business_date) business_date
                FROM {page_table}
                {where_clause}
                ORDER BY business_date DESC, version_no DESC, id DESC
            ) AS archive_dates
            """
            .format(
                page_table=_qualified_table("market_daily_page"),
                where_clause=filters["where"],
            )
        ).bindparams(
            *filters["bindparams"]
        )
        result = await self.session.execute(statement)
        return int(result.scalar_one())

    @staticmethod
    def _row_to_dict(row: object) -> dict:
        mapping = getattr(row, "_mapping", None)
        if mapping is not None:
            return dict(mapping)
        return dict(row)  # type: ignore[arg-type]

    @staticmethod
    def _first_row(result: object) -> object | None:
        if hasattr(result, "one_or_none"):
            return result.one_or_none()  # type: ignore[no-any-return]
        rows = result.all()  # type: ignore[no-any-return]
        return rows[0] if rows else None

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
            clauses.append("business_date >= :from_date")
            params.append(bindparam("from_date", from_date))
        if to_date is not None:
            clauses.append("business_date <= :to_date")
            params.append(bindparam("to_date", to_date))
        if status is not None:
            clauses.append(f"status = CAST(:status AS {_qualified_table('page_status_enum')})")
            params.append(bindparam("status", status))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return {"where": where, "bindparams": params}


__all__ = ["PageSnapshotRepository"]
