from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    BatchJobMarketContextCreateParams,
    BatchJobMarketContextRecord,
)


class MarketContextRepository(PostgresRepository):
    """Persist immutable batch cutoffs and mutable per-market outcomes."""

    async def list_for_job(self, job_id: int) -> list[BatchJobMarketContextRecord]:
        statement = text(
            """
            SELECT
                id AS market_context_id,
                batch_job_id,
                market_type,
                expected_session_date,
                actual_index_source_date,
                session_close_at,
                news_window_start_at,
                news_window_end_at,
                news_coverage_complete,
                created_at,
                updated_at
            FROM {context_table}
            WHERE batch_job_id = :job_id
            ORDER BY market_type
            """.format(context_table=qualify_db_identifier('batch_job_market_context'))
        )
        result = await self.session.execute(statement, {'job_id': job_id})
        return self._models_from_mappings(
            BatchJobMarketContextRecord,
            result.mappings().all(),
        )

    async def get_latest_for_business_date(
        self,
        *,
        business_date: date,
        market_type: str,
        exclude_job_id: int,
    ) -> BatchJobMarketContextRecord | None:
        statement = text(
            """
            SELECT
                context.id AS market_context_id,
                context.batch_job_id,
                context.market_type,
                context.expected_session_date,
                context.actual_index_source_date,
                context.session_close_at,
                context.news_window_start_at,
                context.news_window_end_at,
                context.news_coverage_complete,
                context.created_at,
                context.updated_at
            FROM {context_table} context
            JOIN {job_table} job ON job.id = context.batch_job_id
            WHERE job.business_date = :business_date
              AND context.market_type = CAST(:market_type AS {market_type_enum})
              AND context.batch_job_id <> :exclude_job_id
            ORDER BY context.created_at DESC, context.id DESC
            LIMIT 1
            """.format(
                context_table=qualify_db_identifier('batch_job_market_context'),
                job_table=qualify_db_identifier('batch_job'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'business_date': business_date,
                'market_type': market_type,
                'exclude_job_id': exclude_job_id,
            },
        )
        row = result.mappings().one_or_none()
        return (
            self._model_from_mapping(BatchJobMarketContextRecord, row) if row else None
        )

    async def get_latest_complete_coverage_end(
        self,
        *,
        market_type: str,
        at_or_before: datetime,
    ) -> datetime | None:
        statement = text(
            """
            SELECT news_window_end_at
            FROM {context_table}
            WHERE market_type = CAST(:market_type AS {market_type_enum})
              AND news_coverage_complete
              AND news_window_end_at <= :at_or_before
            ORDER BY news_window_end_at DESC, id DESC
            LIMIT 1
            """.format(
                context_table=qualify_db_identifier('batch_job_market_context'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {'market_type': market_type, 'at_or_before': at_or_before},
        )
        return result.scalar_one_or_none()

    async def insert_if_absent(
        self,
        params: BatchJobMarketContextCreateParams,
    ) -> None:
        statement = text(
            """
            INSERT INTO {context_table} (
                batch_job_id,
                market_type,
                expected_session_date,
                session_close_at,
                news_window_start_at,
                news_window_end_at,
                news_coverage_complete
            )
            VALUES (
                :batch_job_id,
                CAST(:market_type AS {market_type_enum}),
                :expected_session_date,
                :session_close_at,
                :news_window_start_at,
                :news_window_end_at,
                FALSE
            )
            ON CONFLICT (batch_job_id, market_type) DO NOTHING
            """.format(
                context_table=qualify_db_identifier('batch_job_market_context'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        await self.session.execute(
            statement,
            {
                'batch_job_id': params.batch_job_id,
                'market_type': params.market_type,
                'expected_session_date': params.expected_session_date,
                'session_close_at': params.session_close_at,
                'news_window_start_at': params.news_window_start_at,
                'news_window_end_at': params.news_window_end_at,
            },
        )

    async def set_news_coverage_complete(
        self,
        *,
        job_id: int,
        market_type: str,
        coverage_complete: bool,
    ) -> None:
        statement = text(
            """
            UPDATE {context_table}
            SET
                news_coverage_complete = :coverage_complete,
                updated_at = now()
            WHERE batch_job_id = :job_id
              AND market_type = CAST(:market_type AS {market_type_enum})
            """.format(
                context_table=qualify_db_identifier('batch_job_market_context'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'market_type': market_type,
                'coverage_complete': coverage_complete,
            },
        )

    async def set_actual_index_source_date(
        self,
        *,
        job_id: int,
        market_type: str,
        source_date: date | None,
    ) -> None:
        statement = text(
            """
            UPDATE {context_table}
            SET
                actual_index_source_date = :source_date,
                updated_at = now()
            WHERE batch_job_id = :job_id
              AND market_type = CAST(:market_type AS {market_type_enum})
            """.format(
                context_table=qualify_db_identifier('batch_job_market_context'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'market_type': market_type,
                'source_date': source_date,
            },
        )


__all__ = ['MarketContextRepository']
