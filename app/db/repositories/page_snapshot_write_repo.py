from __future__ import annotations

import json
from datetime import date
from typing import Any

from sqlalchemy import text

from app.core.settings import get_settings

from .base import PostgresRepository


def _qualified_table(table_name: str) -> str:
    return f"{get_settings().database_schema}.{table_name}"


class PageSnapshotWriteRepository(PostgresRepository):
    async def get_next_version_no(self, business_date: date) -> int:
        statement = text(
            """
            SELECT COALESCE(MAX(version_no), 0) + 1
            FROM {page_table}
            WHERE business_date = :business_date
            """
            .format(page_table=_qualified_table("market_daily_page"))
        )
        result = await self.session.execute(statement, {"business_date": business_date})
        return int(result.scalar_one())

    async def create_page(
        self,
        *,
        business_date: date,
        version_no: int,
        page_title: str,
        status: str,
        global_headline: str | None,
        partial_message: str | None,
        raw_news_count: int,
        processed_news_count: int,
        cluster_count: int,
        batch_job_id: int,
        metadata_json: dict[str, Any],
    ) -> int:
        statement = text(
            """
            INSERT INTO {page_table} (
                business_date,
                version_no,
                page_title,
                status,
                global_headline,
                partial_message,
                raw_news_count,
                processed_news_count,
                cluster_count,
                batch_job_id,
                metadata_json
            )
            VALUES (
                :business_date,
                :version_no,
                :page_title,
                CAST(:status AS {status_enum}),
                :global_headline,
                :partial_message,
                :raw_news_count,
                :processed_news_count,
                :cluster_count,
                :batch_job_id,
                CAST(:metadata_json AS JSONB)
            )
            RETURNING id
            """
            .format(
                page_table=_qualified_table("market_daily_page"),
                status_enum=_qualified_table("page_status_enum"),
            )
        )
        result = await self.session.execute(
            statement,
            {
                "business_date": business_date,
                "version_no": version_no,
                "page_title": page_title,
                "status": status,
                "global_headline": global_headline,
                "partial_message": partial_message,
                "raw_news_count": raw_news_count,
                "processed_news_count": processed_news_count,
                "cluster_count": cluster_count,
                "batch_job_id": batch_job_id,
                "metadata_json": json.dumps(metadata_json),
            },
        )
        page_id = int(result.scalar_one())
        return page_id

    async def create_page_market(
        self,
        *,
        page_id: int,
        market_type: str,
        display_order: int,
        market_label: str,
        summary_title: str | None,
        summary_body: str | None,
        analysis_background_json: list[str],
        analysis_key_themes_json: list[str],
        analysis_outlook: str | None,
        raw_news_count: int,
        processed_news_count: int,
        cluster_count: int,
        partial_message: str | None,
        metadata_json: dict[str, Any],
    ) -> int:
        statement = text(
            """
            INSERT INTO {page_market_table} (
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
                partial_message,
                metadata_json
            )
            VALUES (
                :page_id,
                CAST(:market_type AS {market_type_enum}),
                :display_order,
                :market_label,
                :summary_title,
                :summary_body,
                CAST(:analysis_background_json AS JSONB),
                CAST(:analysis_key_themes_json AS JSONB),
                :analysis_outlook,
                :raw_news_count,
                :processed_news_count,
                :cluster_count,
                :partial_message,
                CAST(:metadata_json AS JSONB)
            )
            RETURNING id
            """
            .format(
                page_market_table=_qualified_table("market_daily_page_market"),
                market_type_enum=_qualified_table("market_type_enum"),
            )
        )
        result = await self.session.execute(
            statement,
            {
                "page_id": page_id,
                "market_type": market_type,
                "display_order": display_order,
                "market_label": market_label,
                "summary_title": summary_title,
                "summary_body": summary_body,
                "analysis_background_json": json.dumps(analysis_background_json),
                "analysis_key_themes_json": json.dumps(analysis_key_themes_json),
                "analysis_outlook": analysis_outlook,
                "raw_news_count": raw_news_count,
                "processed_news_count": processed_news_count,
                "cluster_count": cluster_count,
                "partial_message": partial_message,
                "metadata_json": json.dumps(metadata_json),
            },
        )
        page_market_id = int(result.scalar_one())
        return page_market_id

    async def insert_page_market_index(self, params: dict[str, Any]) -> None:
        statement = text(
            """
            INSERT INTO {index_table} (
                page_market_id,
                market_index_daily_id,
                display_order,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                high_price,
                low_price,
                currency_code
            )
            VALUES (
                :page_market_id,
                :market_index_daily_id,
                :display_order,
                :index_code,
                :index_name,
                :close_price,
                :change_value,
                :change_percent,
                :high_price,
                :low_price,
                :currency_code
            )
            """
            .format(index_table=_qualified_table("market_daily_page_market_index"))
        )
        await self.session.execute(statement, params)

    async def insert_page_market_cluster(self, params: dict[str, Any]) -> None:
        statement = text(
            """
            INSERT INTO {cluster_table} (
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
            )
            VALUES (
                :page_market_id,
                :cluster_id,
                :cluster_uid,
                :display_order,
                :title,
                :summary,
                :article_count,
                CAST(:tags_json AS JSONB),
                :representative_article_id,
                :representative_title,
                :representative_publisher_name,
                :representative_published_at,
                :representative_origin_link,
                :representative_naver_link
            )
            """
            .format(cluster_table=_qualified_table("market_daily_page_market_cluster"))
        )
        payload = dict(params)
        payload["tags_json"] = json.dumps(payload["tags_json"])
        await self.session.execute(statement, payload)

    async def insert_page_article_link(self, params: dict[str, Any]) -> None:
        statement = text(
            """
            INSERT INTO {article_table} (
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
            )
            VALUES (
                :page_market_id,
                :display_order,
                :processed_article_id,
                :cluster_id,
                :cluster_uid,
                :cluster_title,
                :title,
                :publisher_name,
                :published_at,
                :origin_link,
                :naver_link
            )
            """
            .format(article_table=_qualified_table("market_daily_page_article_link"))
        )
        await self.session.execute(statement, params)


__all__ = ["PageSnapshotWriteRepository"]
