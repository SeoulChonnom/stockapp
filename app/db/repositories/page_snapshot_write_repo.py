from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import date
from hashlib import sha256
from typing import Any

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository


def _page_version_lock_key(business_date: date) -> int:
    lock_identity = f'market_daily_page:{business_date.isoformat()}'
    digest = sha256(lock_identity.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], byteorder='big', signed=True)


class PageSnapshotWriteRepository(PostgresRepository):
    async def get_next_version_no(self, business_date: date) -> int:
        lock_statement = text('SELECT pg_advisory_xact_lock(:lock_key)')
        await self.session.execute(
            lock_statement,
            {'lock_key': _page_version_lock_key(business_date)},
        )

        statement = text(
            """
            SELECT COALESCE(MAX(version_no), 0) + 1
            FROM {page_table}
            WHERE business_date = :business_date
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        )
        result = await self.session.execute(statement, {'business_date': business_date})
        return int(result.scalar_one())

    async def create_page(
        self,
        *,
        business_date: date,
        version_no: int,
        page_title: str,
        status: str,
        global_headline: str | None,
        search_document: str,
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
                search_document,
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
                :search_document,
                :partial_message,
                :raw_news_count,
                :processed_news_count,
                :cluster_count,
                :batch_job_id,
                CAST(:metadata_json AS JSONB)
            )
            RETURNING id
            """.format(
                page_table=qualify_db_identifier('market_daily_page'),
                status_enum=qualify_db_identifier('page_status_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': business_date,
                'version_no': version_no,
                'page_title': page_title,
                'status': status,
                'global_headline': global_headline,
                'search_document': search_document,
                'partial_message': partial_message,
                'raw_news_count': raw_news_count,
                'processed_news_count': processed_news_count,
                'cluster_count': cluster_count,
                'batch_job_id': batch_job_id,
                'metadata_json': json.dumps(metadata_json),
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
        search_document: str = '',
        expected_session_date: date | None = None,
        actual_index_source_date: date | None = None,
        session_close_at: Any | None = None,
        news_window_start_at: Any | None = None,
        news_window_end_at: Any | None = None,
        news_coverage_complete: bool | None = None,
    ) -> int:
        statement = text(
            """
            INSERT INTO {page_market_table} (
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
                partial_message,
                metadata_json
            )
            VALUES (
                :page_id,
                CAST(:market_type AS {market_type_enum}),
                :expected_session_date,
                :actual_index_source_date,
                :session_close_at,
                :news_window_start_at,
                :news_window_end_at,
                :news_coverage_complete,
                :display_order,
                :market_label,
                :summary_title,
                :summary_body,
                CAST(:analysis_background_json AS JSONB),
                CAST(:analysis_key_themes_json AS JSONB),
                :analysis_outlook,
                :search_document,
                :raw_news_count,
                :processed_news_count,
                :cluster_count,
                :partial_message,
                CAST(:metadata_json AS JSONB)
            )
            RETURNING id
            """.format(
                page_market_table=qualify_db_identifier('market_daily_page_market'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'page_id': page_id,
                'market_type': market_type,
                'expected_session_date': expected_session_date,
                'actual_index_source_date': actual_index_source_date,
                'session_close_at': session_close_at,
                'news_window_start_at': news_window_start_at,
                'news_window_end_at': news_window_end_at,
                'news_coverage_complete': news_coverage_complete,
                'display_order': display_order,
                'market_label': market_label,
                'summary_title': summary_title,
                'summary_body': summary_body,
                'analysis_background_json': json.dumps(analysis_background_json),
                'analysis_key_themes_json': json.dumps(analysis_key_themes_json),
                'analysis_outlook': analysis_outlook,
                'search_document': search_document,
                'raw_news_count': raw_news_count,
                'processed_news_count': processed_news_count,
                'cluster_count': cluster_count,
                'partial_message': partial_message,
                'metadata_json': json.dumps(metadata_json),
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
            )
            VALUES (
                :page_market_id,
                :market_index_daily_id,
                :source_date,
                :expected_session_date,
                :session_close_at,
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
            """.format(
                index_table=qualify_db_identifier('market_daily_page_market_index')
            )
        )
        await self.session.execute(statement, params)

    async def insert_page_market_cluster(self, params: dict[str, Any]) -> int:
        statement = text(
            """
            INSERT INTO {cluster_table} (
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
                representative_naver_link,
                article_grouping_status,
                article_grouping_generated_at,
                article_grouping_issue_code,
                article_grouping_algorithm_version
            )
            VALUES (
                :page_market_id,
                :cluster_id,
                :cluster_uid,
                :display_order,
                :title,
                :summary,
                :search_document,
                :article_count,
                CAST(:tags_json AS JSONB),
                :representative_article_id,
                :representative_title,
                :representative_publisher_name,
                :representative_published_at,
                :representative_origin_link,
                :representative_naver_link,
                :article_grouping_status,
                :article_grouping_generated_at,
                :article_grouping_issue_code,
                :article_grouping_algorithm_version
            )
            """.format(
                cluster_table=qualify_db_identifier('market_daily_page_market_cluster')
            )
            + '\n            RETURNING id'
        )
        payload = dict(params)
        payload.setdefault('search_document', '')
        payload.setdefault('article_grouping_status', 'UNAVAILABLE')
        payload.setdefault('article_grouping_generated_at', None)
        payload.setdefault('article_grouping_issue_code', 'SIMILARITY_GROUPING_FAILED')
        payload.setdefault('article_grouping_algorithm_version', None)
        payload['tags_json'] = json.dumps(payload['tags_json'])
        result = await self.session.execute(statement, payload)
        return int(result.scalar_one())

    async def insert_page_market_cluster_themes(
        self,
        page_market_cluster_id: int,
        themes: Sequence[Mapping[str, Any] | Any],
    ) -> None:
        """Insert ranked theme rows for one immutable snapshot cluster.

        The caller owns the surrounding transaction.  Snapshot rows are never
        re-ranked or replaced here: source ranks are validated and persisted
        exactly as supplied, while PostgreSQL enforces the catalog FK and
        uniqueness constraints.
        """
        if (
            isinstance(page_market_cluster_id, bool)
            or not isinstance(page_market_cluster_id, int)
            or page_market_cluster_id <= 0
        ):
            raise ValueError('page_market_cluster_id must be a positive integer')

        try:
            raw_themes = list(themes)
        except TypeError as exc:
            raise ValueError('snapshot themes must be a sequence') from exc
        if len(raw_themes) > 3:
            raise ValueError('snapshot themes must contain at most 3 items')

        normalized: list[dict[str, Any]] = []
        seen_codes: set[str] = set()
        seen_ranks: set[int] = set()
        for theme in raw_themes:
            if isinstance(theme, Mapping):
                theme_code = theme.get('theme_code')
                rank = theme.get('rank')
            else:
                theme_code = getattr(theme, 'theme_code', None)
                rank = getattr(theme, 'rank', None)
            if not isinstance(theme_code, str) or not theme_code:
                raise ValueError('snapshot theme code must be a nonempty string')
            if isinstance(rank, bool) or not isinstance(rank, int):
                raise ValueError('snapshot theme rank must be an integer')
            if rank not in {1, 2, 3}:
                raise ValueError('snapshot theme rank must be between 1 and 3')
            if theme_code in seen_codes:
                raise ValueError('snapshot theme codes must be unique')
            if rank in seen_ranks:
                raise ValueError('snapshot theme ranks must be unique')
            seen_codes.add(theme_code)
            seen_ranks.add(rank)
            normalized.append(
                {
                    'page_market_cluster_id': page_market_cluster_id,
                    'theme_code': theme_code,
                    'rank': rank,
                }
            )

        if not normalized:
            return

        statement = text(
            """
            INSERT INTO {theme_table} (
                page_market_cluster_id,
                theme_code,
                rank
            )
            VALUES (
                :page_market_cluster_id,
                :theme_code,
                :rank
            )
            """.format(
                theme_table=qualify_db_identifier(
                    'market_daily_page_market_cluster_theme'
                )
            )
        )
        await self.session.execute(statement, normalized)

    async def insert_page_article_link(self, params: dict[str, Any]) -> None:
        for required_identity in ('processed_article_id', 'cluster_uid'):
            if params.get(required_identity) is None:
                raise ValueError(
                    f'{required_identity} must not be null in a current snapshot'
                )
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
                naver_link,
                similar_group_rank,
                is_similar_group_representative,
                exact_duplicate_count
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
                :naver_link,
                :similar_group_rank,
                :is_similar_group_representative,
                :exact_duplicate_count
            )
            """.format(
                article_table=qualify_db_identifier('market_daily_page_article_link')
            )
        )
        payload = dict(params)
        payload.setdefault('similar_group_rank', None)
        payload.setdefault('is_similar_group_representative', True)
        payload.setdefault('exact_duplicate_count', 0)
        await self.session.execute(statement, payload)


__all__ = ['PageSnapshotWriteRepository']
