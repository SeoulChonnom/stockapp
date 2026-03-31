from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import bindparam, text

from app.core.settings import get_settings

from .base import PostgresRepository
from .projections import (
    BatchJobCreateParams,
    BatchJobListResult,
    BatchJobRecord,
    BatchJobSummary,
)


def _qualified_table(table_name: str) -> str:
    return f"{get_settings().database_schema}.{table_name}"


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
            FROM {batch_job_table}
            WHERE id = :job_id
            """
            .format(batch_job_table=_qualified_table("batch_job"))
        ).bindparams(bindparam("job_id", job_id))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def has_active_job_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            SELECT id
            FROM {batch_job_table}
            WHERE business_date = :business_date
              AND status IN ('PENDING', 'RUNNING')
            LIMIT 1
            """
            .format(batch_job_table=_qualified_table("batch_job"))
        ).bindparams(bindparam("business_date", business_date))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def has_completed_page_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            SELECT id
            FROM {page_table}
            WHERE business_date = :business_date
            LIMIT 1
            """
            .format(page_table=_qualified_table("market_daily_page"))
        ).bindparams(bindparam("business_date", business_date))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def create_job(self, params: BatchJobCreateParams) -> BatchJobRecord:
        statement = text(
            """
            INSERT INTO {batch_job_table} (
                business_date,
                status,
                trigger_type,
                triggered_by_user_id,
                force_run,
                rebuild_page_only
            )
            VALUES (
                :business_date,
                CAST(:status AS {status_enum}),
                CAST(:trigger_type AS {trigger_enum}),
                :triggered_by_user_id,
                :force_run,
                :rebuild_page_only
            )
            RETURNING
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
            """
            .format(
                batch_job_table=_qualified_table("batch_job"),
                status_enum=_qualified_table("batch_job_status_enum"),
                trigger_enum=_qualified_table("batch_trigger_type_enum"),
            )
        ).bindparams(
            bindparam("business_date", params.business_date),
            bindparam("status", params.status),
            bindparam("trigger_type", params.trigger_type),
            bindparam("triggered_by_user_id", params.triggered_by_user_id),
            bindparam("force_run", params.force_run),
            bindparam("rebuild_page_only", params.rebuild_page_only),
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        row = result.mappings().one()
        return self._model_from_mapping(BatchJobRecord, row)

    async def add_event(
        self,
        *,
        job_id: int,
        step_code: str,
        level: str,
        message: str,
        context_json: dict[str, Any] | None = None,
    ) -> None:
        statement = text(
            """
            INSERT INTO {batch_job_event_table} (
                batch_job_id,
                step_code,
                level,
                message,
                context_json
            )
            VALUES (
                :job_id,
                :step_code,
                CAST(:level AS {level_enum}),
                :message,
                CAST(:context_json AS JSONB)
            )
            """
            .format(
                batch_job_event_table=_qualified_table("batch_job_event"),
                level_enum=_qualified_table("event_level_enum"),
            )
        )
        await self.session.execute(
            statement,
            {
                "job_id": job_id,
                "step_code": step_code,
                "level": level,
                "message": message,
                "context_json": json.dumps(context_json or {}),
            },
        )
        await self.session.commit()

    async def mark_job_completed(
        self,
        *,
        job_id: int,
        status: str,
        partial_message: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        log_summary: str | None = None,
        raw_news_count: int = 0,
        processed_news_count: int = 0,
        cluster_count: int = 0,
        page_id: int | None = None,
        page_version_no: int | None = None,
    ) -> None:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                status = CAST(:status AS {status_enum}),
                ended_at = now(),
                duration_seconds = GREATEST(
                    EXTRACT(EPOCH FROM (now() - started_at))::int,
                    0
                ),
                raw_news_count = :raw_news_count,
                processed_news_count = :processed_news_count,
                cluster_count = :cluster_count,
                page_id = :page_id,
                page_version_no = :page_version_no,
                partial_message = :partial_message,
                error_code = :error_code,
                error_message = :error_message,
                log_summary = :log_summary,
                updated_at = now()
            WHERE id = :job_id
            """
            .format(
                batch_job_table=_qualified_table("batch_job"),
                status_enum=_qualified_table("batch_job_status_enum"),
            )
        )
        await self.session.execute(
            statement,
            {
                "job_id": job_id,
                "status": status,
                "raw_news_count": raw_news_count,
                "processed_news_count": processed_news_count,
                "cluster_count": cluster_count,
                "page_id": page_id,
                "page_version_no": page_version_no,
                "partial_message": partial_message,
                "error_code": error_code,
                "error_message": error_message,
                "log_summary": log_summary,
            },
        )
        await self.session.commit()

    async def mark_job_failed(self, *, job_id: int, error_code: str, error_message: str) -> None:
        await self.mark_job_completed(
            job_id=job_id,
            status="FAILED",
            error_code=error_code,
            error_message=error_message,
            log_summary=error_message,
        )

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
            from_date=from_date,
            to_date=to_date,
            status=status,
        )

        count_statement = text(
            f"""
            SELECT COUNT(*) AS total_count
            FROM {_qualified_table("batch_job")}
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
            FROM {_qualified_table("batch_job")}
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
            FROM {_qualified_table("batch_job")}
            {where_sql}
            ORDER BY business_date DESC, started_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self.session.execute(
            statement,
            {**params, "limit": size, "offset": offset},
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
            clauses.append(
                f"status = CAST(:status AS {_qualified_table('batch_job_status_enum')})"
            )
            params["status"] = status

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params


__all__ = ["BatchJobRepository"]
