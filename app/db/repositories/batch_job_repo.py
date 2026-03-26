from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from .base import PostgresRepository
from .projections import BatchJobListResult, BatchJobRecord, BatchJobSummary


class BatchJobRepository(PostgresRepository):
    async def get_job_by_id(self, job_id: int) -> BatchJobRecord | None:
        statement = text(
            """
            SELECT
                id AS job_id,
                job_name,
                business_date,
                status,
                trigger_type,
                triggered_by_user_id,
                force_run,
                rebuild_page_only,
                started_at,
                ended_at,
                duration_seconds,
                market_scope,
                raw_news_count,
                processed_news_count,
                cluster_count,
                page_id,
                page_version_no,
                partial_message,
                error_code,
                error_message,
                log_summary,
                created_at,
                updated_at
            FROM batch_job
            WHERE id = :job_id
            """
        )
        result = await self.session.execute(statement, {"job_id": job_id})
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def list_jobs(
        self,
        *,
        from_date: date | None = None,
        to_date: date | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> BatchJobListResult:
        page, size, offset = self._normalize_pagination(page, size, max_size=100)
        where_sql, params = self._build_filters(
            from_date=from_date, to_date=to_date, status=status
        )

        count_statement = text(
            f"""
            SELECT COUNT(*) AS total_count
            FROM batch_job
            {where_sql}
            """
        )
        count_result = await self.session.execute(count_statement, params)
        total_count = int(count_result.scalar_one())

        summary_statement = text(
            f"""
            SELECT
                COALESCE(COUNT(*) FILTER (WHERE status = 'SUCCESS'), 0) AS success_count,
                COALESCE(COUNT(*) FILTER (WHERE status = 'PARTIAL'), 0) AS partial_count,
                COALESCE(COUNT(*) FILTER (WHERE status = 'FAILED'), 0) AS failed_count,
                COALESCE(ROUND(AVG(duration_seconds))::int, 0) AS avg_duration_seconds
            FROM batch_job
            {where_sql}
            """
        )
        summary_result = await self.session.execute(summary_statement, params)
        summary_row = summary_result.mappings().one()
        summary = self._model_from_mapping(BatchJobSummary, summary_row)

        statement = text(
            f"""
            SELECT
                id AS job_id,
                job_name,
                business_date,
                status,
                started_at,
                ended_at,
                duration_seconds,
                market_scope,
                raw_news_count,
                processed_news_count,
                cluster_count,
                page_id,
                page_version_no,
                partial_message
            FROM batch_job
            {where_sql}
            ORDER BY business_date DESC, started_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self.session.execute(
            statement, {**params, "limit": size, "offset": offset}
        )
        items = self._models_from_mappings(BatchJobRecord, result.mappings().all())
        return BatchJobListResult(
            items=items,
            page=page,
            size=size,
            total_count=total_count,
            summary=summary,
        )

    @staticmethod
    def _build_filters(
        *,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
    ) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if from_date is not None:
            clauses.append("business_date >= :from_date")
            params["from_date"] = from_date
        if to_date is not None:
            clauses.append("business_date <= :to_date")
            params["to_date"] = to_date
        if status is not None:
            clauses.append("status = :status")
            params["status"] = status

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params
