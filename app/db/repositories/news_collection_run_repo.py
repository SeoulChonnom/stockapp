from __future__ import annotations

from datetime import datetime

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    NewsCollectionKeywordDiagnosticParams,
    NewsCollectionRunRecord,
    NewsCoverageInterval,
)


class NewsCollectionRunRepository(PostgresRepository):
    async def get_by_job_id(self, job_id: int) -> NewsCollectionRunRecord | None:
        return await self._get_one('run.batch_job_id = :job_id', {'job_id': job_id})

    async def get_by_window(
        self,
        *,
        provider_name: str,
        window_start_at: datetime,
        window_end_at: datetime,
    ) -> NewsCollectionRunRecord | None:
        return await self._get_one(
            """
            run.provider_name = :provider_name
            AND run.window_start_at = :window_start_at
            AND run.window_end_at = :window_end_at
            """,
            {
                'provider_name': provider_name,
                'window_start_at': window_start_at,
                'window_end_at': window_end_at,
            },
        )

    async def list_slot_ends_between(
        self,
        *,
        provider_name: str,
        from_end_at: datetime,
        to_end_at: datetime,
    ) -> set[datetime]:
        """Return the slot ends already enqueued in a range, gaps excluded.

        A slot the scheduler never asked for leaves no row at all, so the
        difference between this set and the expected grid is exactly the
        collection that was lost while the app was unreachable.
        """
        statement = text(
            """
            SELECT DISTINCT window_end_at
            FROM {run_table}
            WHERE provider_name = :provider_name
              AND window_end_at >= :from_end_at
              AND window_end_at <= :to_end_at
            """.format(run_table=qualify_db_identifier('news_collection_run'))
        )
        result = await self.session.execute(
            statement,
            {
                'provider_name': provider_name,
                'from_end_at': from_end_at,
                'to_end_at': to_end_at,
            },
        )
        return {row['window_end_at'] for row in result.mappings().all()}

    async def _get_one(
        self,
        predicate: str,
        params: dict[str, object],
    ) -> NewsCollectionRunRecord | None:
        statement = text(
            """
            SELECT
                run.id AS run_id,
                run.batch_job_id,
                run.provider_name,
                run.window_start_at,
                run.window_end_at,
                run.query_start_at,
                run.query_end_at,
                run.total_keyword_count,
                run.completed_keyword_count,
                run.fetched_count,
                run.matched_count,
                run.inserted_count,
                run.coverage_complete,
                run.created_at,
                run.updated_at
            FROM {run_table} run
            WHERE {predicate}
            """.format(
                run_table=qualify_db_identifier('news_collection_run'),
                predicate=predicate,
            )
        )
        result = await self.session.execute(statement, params)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(NewsCollectionRunRecord, row) if row else None

    async def create_run(
        self,
        *,
        batch_job_id: int,
        provider_name: str,
        window_start_at: datetime,
        window_end_at: datetime,
        query_start_at: datetime,
        query_end_at: datetime,
    ) -> NewsCollectionRunRecord:
        statement = text(
            """
            INSERT INTO {run_table} (
                batch_job_id,
                provider_name,
                window_start_at,
                window_end_at,
                query_start_at,
                query_end_at
            )
            VALUES (
                :batch_job_id,
                :provider_name,
                :window_start_at,
                :window_end_at,
                :query_start_at,
                :query_end_at
            )
            RETURNING
                id AS run_id,
                batch_job_id,
                provider_name,
                window_start_at,
                window_end_at,
                query_start_at,
                query_end_at,
                total_keyword_count,
                completed_keyword_count,
                fetched_count,
                matched_count,
                inserted_count,
                coverage_complete,
                created_at,
                updated_at
            """.format(run_table=qualify_db_identifier('news_collection_run'))
        )
        result = await self.session.execute(
            statement,
            {
                'batch_job_id': batch_job_id,
                'provider_name': provider_name,
                'window_start_at': window_start_at,
                'window_end_at': window_end_at,
                'query_start_at': query_start_at,
                'query_end_at': query_end_at,
            },
        )
        return self._model_from_mapping(
            NewsCollectionRunRecord,
            result.mappings().one(),
        )

    async def upsert_keyword_diagnostic(
        self,
        params: NewsCollectionKeywordDiagnosticParams,
    ) -> None:
        statement = text(
            """
            INSERT INTO {diagnostic_table} (
                news_collection_run_id,
                keyword_id,
                provider_name,
                market_type,
                keyword,
                status,
                fetched_count,
                matched_count,
                inserted_count,
                coverage_complete,
                error_code,
                error_message
            )
            VALUES (
                :news_collection_run_id,
                :keyword_id,
                :provider_name,
                CAST(:market_type AS {market_type_enum}),
                :keyword,
                :status,
                :fetched_count,
                :matched_count,
                :inserted_count,
                :coverage_complete,
                :error_code,
                :error_message
            )
            ON CONFLICT (news_collection_run_id, keyword_id)
            DO UPDATE SET
                status = EXCLUDED.status,
                fetched_count = EXCLUDED.fetched_count,
                matched_count = EXCLUDED.matched_count,
                inserted_count = EXCLUDED.inserted_count,
                coverage_complete = EXCLUDED.coverage_complete,
                error_code = EXCLUDED.error_code,
                error_message = EXCLUDED.error_message,
                updated_at = now()
            """.format(
                diagnostic_table=qualify_db_identifier(
                    'news_collection_keyword_diagnostic'
                ),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        await self.session.execute(
            statement,
            {
                'news_collection_run_id': params.news_collection_run_id,
                'keyword_id': params.keyword_id,
                'provider_name': params.provider_name,
                'market_type': params.market_type,
                'keyword': params.keyword,
                'status': params.status,
                'fetched_count': params.fetched_count,
                'matched_count': params.matched_count,
                'inserted_count': params.inserted_count,
                'coverage_complete': params.coverage_complete,
                'error_code': params.error_code,
                'error_message': params.error_message,
            },
        )

    async def finalize_run(
        self,
        *,
        run_id: int,
        total_keyword_count: int,
        completed_keyword_count: int,
        fetched_count: int,
        matched_count: int,
        inserted_count: int,
        coverage_complete: bool,
    ) -> None:
        statement = text(
            """
            UPDATE {run_table}
            SET
                total_keyword_count = :total_keyword_count,
                completed_keyword_count = :completed_keyword_count,
                fetched_count = :fetched_count,
                matched_count = :matched_count,
                inserted_count = :inserted_count,
                coverage_complete = :coverage_complete,
                updated_at = now()
            WHERE id = :run_id
            """.format(run_table=qualify_db_identifier('news_collection_run'))
        )
        await self.session.execute(
            statement,
            {
                'run_id': run_id,
                'total_keyword_count': total_keyword_count,
                'completed_keyword_count': completed_keyword_count,
                'fetched_count': fetched_count,
                'matched_count': matched_count,
                'inserted_count': inserted_count,
                'coverage_complete': coverage_complete,
            },
        )

    async def list_complete_intervals(
        self,
        *,
        provider_name: str,
        market_type: str,
        window_start_at: datetime,
        window_end_at: datetime,
    ) -> list[NewsCoverageInterval]:
        statement = text(
            """
            SELECT
                run.window_start_at,
                run.window_end_at
            FROM {run_table} run
            JOIN {job_table} job ON job.id = run.batch_job_id
            WHERE run.provider_name = :provider_name
              AND run.window_start_at < :window_end_at
              AND run.window_end_at > :window_start_at
              AND job.status IN ('SUCCESS', 'PARTIAL')
              AND EXISTS (
                  SELECT 1
                  FROM {diagnostic_table} diagnostic
                  WHERE diagnostic.news_collection_run_id = run.id
                    AND diagnostic.market_type = CAST(
                        :market_type AS {market_type_enum}
                    )
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM {diagnostic_table} diagnostic
                  WHERE diagnostic.news_collection_run_id = run.id
                    AND diagnostic.market_type = CAST(
                        :market_type AS {market_type_enum}
                    )
                    AND (
                        diagnostic.status <> 'SUCCESS'
                        OR NOT diagnostic.coverage_complete
                    )
              )
            ORDER BY run.window_start_at, run.window_end_at
            """.format(
                run_table=qualify_db_identifier('news_collection_run'),
                job_table=qualify_db_identifier('batch_job'),
                diagnostic_table=qualify_db_identifier(
                    'news_collection_keyword_diagnostic'
                ),
                market_type_enum=qualify_db_identifier('market_type_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'provider_name': provider_name,
                'market_type': market_type,
                'window_start_at': window_start_at,
                'window_end_at': window_end_at,
            },
        )
        return self._models_from_mappings(
            NewsCoverageInterval,
            result.mappings().all(),
        )


def intervals_cover_window(
    intervals: list[NewsCoverageInterval],
    *,
    window_start_at: datetime,
    window_end_at: datetime,
) -> bool:
    if window_start_at >= window_end_at:
        return True
    covered_until = window_start_at
    for interval in intervals:
        if interval.window_end_at <= covered_until:
            continue
        if interval.window_start_at > covered_until:
            return False
        covered_until = max(covered_until, interval.window_end_at)
        if covered_until >= window_end_at:
            return True
    return False


__all__ = ['NewsCollectionRunRepository', 'intervals_cover_window']
