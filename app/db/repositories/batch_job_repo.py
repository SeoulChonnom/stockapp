from __future__ import annotations

import json
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text  # pyright: ignore[reportMissingImports]
from sqlalchemy.exc import IntegrityError  # pyright: ignore[reportMissingImports]

from app.batch.exceptions import BatchLeaseLostError
from app.db.enums import BatchJobStatus, BatchJobType, BatchRunMode, BatchStepStatus
from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import (
    BatchJobCreateParams,
    BatchJobListResult,
    BatchJobRecord,
    BatchJobSummary,
    BatchLeaseRecoveryResult,
    BatchPageSource,
)
from app.db.repositories.sql_fragments import (
    DURATION_SECONDS_EXPR,
    STEP_DURATION_MS_EXPR,
    lease_null_assignments_sql,
)

_BATCH_JOB_COLUMNS: tuple[str, ...] = (
    'id AS job_id',
    'job_name',
    'business_date',
    'status',
    'trigger_type',
    'triggered_by_user_id',
    'force_run',
    'rebuild_page_only',
    'run_mode',
    'source_job_id',
    'source_page_id',
    'idempotency_key',
    'queued_at',
    'available_at',
    'attempt_count',
    'max_attempts',
    'lease_owner',
    'lease_token',
    'lease_expires_at',
    'heartbeat_at',
    'current_step',
    'checkpoint_json',
    'started_at',
    'ended_at',
    'duration_seconds',
    'market_scope',
    'raw_news_count',
    'processed_news_count',
    'cluster_count',
    'ai_target_count',
    'ai_attempted_count',
    'ai_success_count',
    'ai_fallback_count',
    'ai_failed_count',
    'ai_recovered_count',
    'page_id',
    'page_version_no',
    'partial_message',
    'error_code',
    'error_message',
    'log_summary',
    'created_at',
    'updated_at',
)
_BATCH_JOB_COLUMNS_SQL = ',\n                '.join(_BATCH_JOB_COLUMNS)
_BATCH_JOB_COLUMNS_SQL_JOB_PREFIXED = ',\n                '.join(
    f'job.{column.split(" AS ")[0]} AS {column.split(" AS ")[1]}'
    if ' AS ' in column
    else f'job.{column}'
    for column in _BATCH_JOB_COLUMNS
)

_STATUS_PENDING = BatchJobStatus.PENDING.value
_STATUS_RUNNING = BatchJobStatus.RUNNING.value
_STATUS_SUCCESS = BatchJobStatus.SUCCESS.value
_STATUS_PARTIAL = BatchJobStatus.PARTIAL.value
_STATUS_FAILED = BatchJobStatus.FAILED.value


class BatchJobRepository(PostgresRepository):
    def __init__(
        self,
        session: Any,
        *,
        lease_token: UUID | None = None,
    ) -> None:
        super().__init__(session)
        self._lease_token = lease_token

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def get_job_by_id(self, job_id: int) -> BatchJobRecord | None:
        statement = text(
            """
            SELECT
                {columns}
            FROM {batch_job_table}
            WHERE id = :job_id
            """.format(
                columns=_BATCH_JOB_COLUMNS_SQL,
                batch_job_table=qualify_db_identifier('batch_job'),
            )
        ).bindparams(bindparam('job_id', job_id))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def get_job_by_idempotency_key(
        self, idempotency_key: str
    ) -> BatchJobRecord | None:
        statement = text(
            """
            SELECT
                {columns}
            FROM {batch_job_table}
            WHERE idempotency_key = :idempotency_key
            """.format(
                columns=_BATCH_JOB_COLUMNS_SQL,
                batch_job_table=qualify_db_identifier('batch_job'),
            )
        ).bindparams(bindparam('idempotency_key', idempotency_key))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def retry_failed_job(self, job_id: int) -> BatchJobRecord | None:
        """Requeue a FAILED job in place so an idempotent replay can retry it."""
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                status = '{status_pending}',
                attempt_count = 0,
                available_at = now(),
                error_code = NULL,
                error_message = NULL,
                {lease_null_assignments}
                updated_at = now()
            WHERE id = :job_id
              AND status = '{status_failed}'
            RETURNING
                {columns}
            """.format(
                columns=_BATCH_JOB_COLUMNS_SQL,
                batch_job_table=qualify_db_identifier('batch_job'),
                status_pending=_STATUS_PENDING,
                status_failed=_STATUS_FAILED,
                lease_null_assignments=lease_null_assignments_sql(' ' * 16),
            )
        ).bindparams(bindparam('job_id', job_id))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def has_active_job_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            SELECT id
            FROM {batch_job_table}
            WHERE business_date = :business_date
              AND status IN ('{status_pending}', '{status_running}')
              AND run_mode IN ('FULL', 'PAGE_REBUILD')
            LIMIT 1
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_pending=_STATUS_PENDING,
                status_running=_STATUS_RUNNING,
            )
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def has_completed_page_for_business_date(self, business_date: date) -> bool:
        statement = text(
            """
            SELECT id
            FROM {page_table}
            WHERE business_date = :business_date
            LIMIT 1
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        return result.scalar_one_or_none() is not None

    async def get_latest_page_source(
        self, business_date: date
    ) -> BatchPageSource | None:
        statement = text(
            """
            SELECT
                id AS page_id,
                batch_job_id,
                status
            FROM {page_table}
            WHERE business_date = :business_date
            ORDER BY version_no DESC, id DESC
            LIMIT 1
            """.format(page_table=qualify_db_identifier('market_daily_page'))
        ).bindparams(bindparam('business_date', business_date))
        result = await self.session.execute(statement)
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchPageSource, row) if row else None

    async def create_job(self, params: BatchJobCreateParams) -> BatchJobRecord:
        statement = text(
            """
            INSERT INTO {batch_job_table} (
                job_name,
                business_date,
                status,
                trigger_type,
                triggered_by_user_id,
                force_run,
                rebuild_page_only,
                run_mode,
                source_job_id,
                source_page_id,
                idempotency_key,
                max_attempts
            )
            VALUES (
                :job_name,
                :business_date,
                CAST(:status AS {status_enum}),
                CAST(:trigger_type AS {trigger_enum}),
                CAST(:triggered_by_user_id AS TEXT),
                :force_run,
                :rebuild_page_only,
                CAST(:run_mode AS {run_mode_enum}),
                :source_job_id,
                :source_page_id,
                :idempotency_key,
                :max_attempts
            )
            RETURNING
                {columns}
            """.format(
                columns=_BATCH_JOB_COLUMNS_SQL,
                batch_job_table=qualify_db_identifier('batch_job'),
                status_enum=qualify_db_identifier('batch_job_status_enum'),
                trigger_enum=qualify_db_identifier('batch_trigger_type_enum'),
                run_mode_enum=qualify_db_identifier('batch_run_mode_enum'),
            )
        ).bindparams(
            bindparam('business_date', params.business_date),
            bindparam('job_name', params.job_name),
            bindparam('status', params.status),
            bindparam('trigger_type', params.trigger_type),
            bindparam('triggered_by_user_id', params.triggered_by_user_id),
            bindparam('force_run', params.force_run),
            bindparam('rebuild_page_only', params.rebuild_page_only),
            bindparam('run_mode', params.run_mode),
            bindparam('source_job_id', params.source_job_id),
            bindparam('source_page_id', params.source_page_id),
            bindparam('idempotency_key', params.idempotency_key),
            bindparam('max_attempts', params.max_attempts),
        )
        try:
            result = await self.session.execute(statement)
        except IntegrityError:
            await self.rollback()
            raise
        row = result.mappings().one()
        return self._model_from_mapping(BatchJobRecord, row)

    async def claim_next_job(
        self,
        *,
        worker_id: str,
        lease_token: UUID,
        lease_seconds: int,
    ) -> BatchJobRecord | None:
        statement = text(
            """
            WITH candidate AS (
                SELECT id
                FROM {batch_job_table}
                WHERE status = '{status_pending}'
                  AND available_at <= now()
                  AND attempt_count < max_attempts
                ORDER BY available_at, queued_at, id
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE {batch_job_table} AS job
            SET
                status = '{status_running}',
                attempt_count = job.attempt_count + 1,
                lease_owner = :worker_id,
                lease_token = :lease_token,
                lease_expires_at = now() + make_interval(secs => :lease_seconds),
                heartbeat_at = now(),
                started_at = now(),
                ended_at = NULL,
                duration_seconds = NULL,
                error_code = NULL,
                error_message = NULL,
                updated_at = now()
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING
                {columns}
            """.format(
                columns=_BATCH_JOB_COLUMNS_SQL_JOB_PREFIXED,
                batch_job_table=qualify_db_identifier('batch_job'),
                status_pending=_STATUS_PENDING,
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'worker_id': worker_id,
                'lease_token': lease_token,
                'lease_seconds': lease_seconds,
            },
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(BatchJobRecord, row) if row else None

    async def seconds_until_next_actionable_job(self) -> float | None:
        statement = text(
            """
            SELECT GREATEST(
                EXTRACT(EPOCH FROM (MIN(action_at) - now())),
                0
            )
            FROM (
                SELECT available_at AS action_at
                FROM {batch_job_table}
                WHERE status = '{status_pending}'
                  AND attempt_count < max_attempts

                UNION ALL

                SELECT COALESCE(lease_expires_at, now()) AS action_at
                FROM {batch_job_table}
                WHERE status = '{status_running}'
            ) AS actionable_jobs
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_pending=_STATUS_PENDING,
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(statement)
        value = result.scalar_one_or_none()
        return float(value) if value is not None else None

    async def heartbeat_claim(
        self,
        *,
        job_id: int,
        worker_id: str,
        lease_token: UUID,
        lease_seconds: int,
    ) -> bool:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                heartbeat_at = now(),
                lease_expires_at = now() + make_interval(secs => :lease_seconds),
                updated_at = now()
            WHERE id = :job_id
              AND status = '{status_running}'
              AND lease_owner = :worker_id
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            RETURNING id
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'worker_id': worker_id,
                'lease_token': lease_token,
                'lease_seconds': lease_seconds,
            },
        )
        return result.scalar_one_or_none() is not None

    async def begin_step(
        self,
        *,
        job_id: int,
        lease_token: UUID,
        step_code: str,
    ) -> int | None:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET current_step = :step_code, updated_at = now()
            WHERE id = :job_id
              AND status = '{status_running}'
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            RETURNING id
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'lease_token': lease_token,
                'step_code': step_code,
            },
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._insert_step_run(job_id=job_id, step_code=step_code)

    async def _insert_step_run(self, *, job_id: int, step_code: str) -> int:
        statement = text(
            """
            INSERT INTO {step_run_table} (batch_job_id, step_code, seq, status)
            SELECT
                :job_id,
                :step_code,
                COALESCE(MAX(seq), 0) + 1,
                '{status_running}'
            FROM {step_run_table}
            WHERE batch_job_id = :job_id
            RETURNING id
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
                status_running=BatchStepStatus.RUNNING.value,
            )
        )
        result = await self.session.execute(
            statement,
            {'job_id': job_id, 'step_code': step_code},
        )
        return int(result.scalar_one())

    async def finish_step_run(self, *, step_run_id: int, status: str) -> bool:
        statement = text(
            """
            UPDATE {step_run_table}
            SET
                status = CAST(:status AS batch_step_status_enum),
                ended_at = now(),
                duration_ms = {step_duration_ms_expr},
                updated_at = now()
            WHERE id = :step_run_id
              AND status = '{status_running}'
            RETURNING id
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
                step_duration_ms_expr=STEP_DURATION_MS_EXPR,
                status_running=BatchStepStatus.RUNNING.value,
            )
        )
        result = await self.session.execute(
            statement,
            {'step_run_id': step_run_id, 'status': status},
        )
        return result.scalar_one_or_none() is not None

    async def save_checkpoint(
        self,
        *,
        job_id: int,
        lease_token: UUID,
        current_step: str,
        checkpoint_json: dict[str, Any],
    ) -> bool:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                current_step = :current_step,
                checkpoint_json = CAST(:checkpoint_json AS JSONB),
                updated_at = now()
            WHERE id = :job_id
              AND status = '{status_running}'
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            RETURNING id
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'lease_token': lease_token,
                'current_step': current_step,
                'checkpoint_json': json.dumps(checkpoint_json),
            },
        )
        return result.scalar_one_or_none() is not None

    async def recover_expired_claims(self) -> BatchLeaseRecoveryResult:
        failed_statement = text(
            """
            WITH failed AS (
                UPDATE {batch_job_table}
                SET
                    status = '{status_failed}',
                    ended_at = now(),
                    duration_seconds = {duration_seconds_expr},
                    {lease_null_assignments}
                    error_code = 'BATCH_ATTEMPTS_EXHAUSTED',
                    error_message = 'Batch job exhausted its worker attempts.',
                    log_summary = 'Batch job exhausted its worker attempts.',
                    updated_at = now()
                WHERE status = '{status_running}'
                  AND (
                      lease_expires_at IS NULL
                      OR lease_expires_at <= now()
                  )
                  AND attempt_count >= max_attempts
                RETURNING id
            )
            SELECT COUNT(*) FROM failed
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                duration_seconds_expr=DURATION_SECONDS_EXPR,
                lease_null_assignments=lease_null_assignments_sql(' ' * 20),
                status_failed=_STATUS_FAILED,
                status_running=_STATUS_RUNNING,
            )
        )
        failed_result = await self.session.execute(failed_statement)
        failed_count = int(failed_result.scalar_one())

        requeued_statement = text(
            """
            WITH requeued AS (
                UPDATE {batch_job_table}
                SET
                    status = '{status_pending}',
                    available_at = now(),
                    {lease_null_assignments}
                    ended_at = NULL,
                    duration_seconds = NULL,
                    error_code = 'BATCH_LEASE_EXPIRED',
                    error_message = 'Worker lease expired; job was requeued.',
                    updated_at = now()
                WHERE status = '{status_running}'
                  AND (
                      lease_expires_at IS NULL
                      OR lease_expires_at <= now()
                  )
                  AND attempt_count < max_attempts
                RETURNING id
            )
            SELECT COUNT(*) FROM requeued
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                lease_null_assignments=lease_null_assignments_sql(' ' * 20),
                status_pending=_STATUS_PENDING,
                status_running=_STATUS_RUNNING,
            )
        )
        requeued_result = await self.session.execute(requeued_statement)
        requeued_count = int(requeued_result.scalar_one())
        return BatchLeaseRecoveryResult(
            requeued_count=requeued_count,
            failed_count=failed_count,
        )

    async def release_failed_claim(
        self,
        *,
        job_id: int,
        lease_token: UUID,
        error_code: str,
        error_message: str,
        retry_delay_seconds: int,
    ) -> str | None:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                status = CAST(
                    CASE
                        WHEN attempt_count >= max_attempts THEN '{status_failed}'
                        ELSE '{status_pending}'
                    END
                    AS {status_enum}
                ),
                available_at = CASE
                    WHEN attempt_count >= max_attempts THEN available_at
                    ELSE now() + make_interval(secs => :retry_delay_seconds)
                END,
                ended_at = CASE
                    WHEN attempt_count >= max_attempts THEN now()
                    ELSE NULL
                END,
                duration_seconds = CASE
                    WHEN attempt_count >= max_attempts
                    THEN {duration_seconds_expr}
                    ELSE NULL
                END,
                {lease_null_assignments}
                error_code = CASE
                    WHEN attempt_count >= max_attempts
                    THEN 'BATCH_ATTEMPTS_EXHAUSTED'
                    ELSE :error_code
                END,
                error_message = :error_message,
                log_summary = :error_message,
                updated_at = now()
            WHERE id = :job_id
              AND status = '{status_running}'
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            RETURNING status::text
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_enum=qualify_db_identifier('batch_job_status_enum'),
                duration_seconds_expr=DURATION_SECONDS_EXPR,
                lease_null_assignments=lease_null_assignments_sql(' ' * 16),
                status_failed=_STATUS_FAILED,
                status_pending=_STATUS_PENDING,
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'lease_token': lease_token,
                'error_code': error_code,
                'error_message': error_message,
                'retry_delay_seconds': retry_delay_seconds,
            },
        )
        return result.scalar_one_or_none()

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
            """.format(
                batch_job_event_table=qualify_db_identifier('batch_job_event'),
                level_enum=qualify_db_identifier('event_level_enum'),
            )
        )
        await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'step_code': step_code,
                'level': level,
                'message': message,
                'context_json': json.dumps(context_json or {}),
            },
        )

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
        ai_target_count: int = 0,
        ai_attempted_count: int = 0,
        ai_success_count: int = 0,
        ai_fallback_count: int = 0,
        ai_failed_count: int = 0,
        page_id: int | None = None,
        page_version_no: int | None = None,
    ) -> None:
        lease_predicate = ''
        if self._lease_token is not None:
            lease_predicate = f"""
              AND status = '{_STATUS_RUNNING}'
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            """
        statement = text(
            """
            UPDATE {batch_job_table}
            SET
                status = CAST(:status AS {status_enum}),
                ended_at = now(),
                duration_seconds = {duration_seconds_expr},
                raw_news_count = :raw_news_count,
                processed_news_count = :processed_news_count,
                cluster_count = :cluster_count,
                ai_target_count = :ai_target_count,
                ai_attempted_count = :ai_attempted_count,
                ai_success_count = :ai_success_count,
                ai_fallback_count = :ai_fallback_count,
                ai_failed_count = :ai_failed_count,
                page_id = :page_id,
                page_version_no = :page_version_no,
                partial_message = :partial_message,
                error_code = :error_code,
                error_message = :error_message,
                log_summary = :log_summary,
                {lease_null_assignments}
                updated_at = now()
            WHERE id = :job_id
            {lease_predicate}
            RETURNING id
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_enum=qualify_db_identifier('batch_job_status_enum'),
                duration_seconds_expr=DURATION_SECONDS_EXPR,
                lease_null_assignments=lease_null_assignments_sql(' ' * 16),
                lease_predicate=lease_predicate,
            )
        )
        params = {
            'job_id': job_id,
            'status': status,
            'raw_news_count': raw_news_count,
            'processed_news_count': processed_news_count,
            'cluster_count': cluster_count,
            'ai_target_count': ai_target_count,
            'ai_attempted_count': ai_attempted_count,
            'ai_success_count': ai_success_count,
            'ai_fallback_count': ai_fallback_count,
            'ai_failed_count': ai_failed_count,
            'page_id': page_id,
            'page_version_no': page_version_no,
            'partial_message': partial_message,
            'error_code': error_code,
            'error_message': error_message,
            'log_summary': log_summary,
        }
        if self._lease_token is not None:
            params['lease_token'] = self._lease_token
        result = await self.session.execute(
            statement,
            params,
        )
        if self._lease_token is not None and result.scalar_one_or_none() is None:
            raise BatchLeaseLostError(
                f'Lease was lost before batch job {job_id} could be completed.'
            )

    async def mark_job_failed(
        self, *, job_id: int, error_code: str, error_message: str
    ) -> None:
        await self.mark_job_completed(
            job_id=job_id,
            status=_STATUS_FAILED,
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
        job_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> BatchJobListResult:
        page, size, offset = self._normalize_pagination(page, size, max_size=100)
        where_sql, params = self._build_filters(
            from_date=from_date,
            to_date=to_date,
            status=status,
            job_type=job_type,
        )

        count_statement = text(
            f"""
            SELECT COUNT(*) AS total_count
            FROM {qualify_db_identifier('batch_job')}
            {where_sql}
            """
        )
        count_result = await self.session.execute(count_statement, params)
        total_count = int(count_result.scalar_one())

        summary_statement = text(
            f"""
            SELECT
                COALESCE(COUNT(*) FILTER (WHERE status = '{_STATUS_SUCCESS}'), 0)
                    AS success_count,
                COALESCE(COUNT(*) FILTER (WHERE status = '{_STATUS_PARTIAL}'), 0)
                    AS partial_count,
                COALESCE(COUNT(*) FILTER (WHERE status = '{_STATUS_FAILED}'), 0)
                    AS failed_count,
                COALESCE(ROUND(AVG(duration_seconds))::int, 0) AS avg_duration_seconds
            FROM {qualify_db_identifier('batch_job')}
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
                run_mode,
                source_job_id,
                source_page_id,
                queued_at,
                available_at,
                attempt_count,
                max_attempts,
                lease_owner,
                lease_expires_at,
                heartbeat_at,
                current_step,
                started_at,
                ended_at,
                duration_seconds,
                market_scope,
                raw_news_count,
                processed_news_count,
                cluster_count,
                ai_target_count,
                ai_attempted_count,
                ai_success_count,
                ai_fallback_count,
                ai_failed_count,
                ai_recovered_count,
                page_id,
                page_version_no,
                partial_message
            FROM {qualify_db_identifier('batch_job')}
            {where_sql}
            ORDER BY business_date DESC, started_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """
        )
        result = await self.session.execute(
            statement,
            {**params, 'limit': size, 'offset': offset},
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
        job_type: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        clauses: list[str] = []
        params: dict[str, Any] = {}

        if from_date is not None:
            clauses.append('business_date >= :from_date')
            params['from_date'] = from_date
        if to_date is not None:
            clauses.append('business_date <= :to_date')
            params['to_date'] = to_date
        if status is not None:
            clauses.append(
                f'status = CAST(:status AS {qualify_db_identifier("batch_job_status_enum")})'
            )
            params['status'] = status
        if job_type == BatchJobType.NEWS_COLLECTION.value:
            run_mode_enum = qualify_db_identifier('batch_run_mode_enum')
            clauses.append(f'run_mode = CAST(:job_type_run_mode AS {run_mode_enum})')
            params['job_type_run_mode'] = BatchRunMode.NEWS_COLLECTION.value
        elif job_type == BatchJobType.MARKET_SNAPSHOT.value:
            run_mode_enum = qualify_db_identifier('batch_run_mode_enum')
            clauses.append(
                'run_mode IN ('
                f'CAST(:job_type_run_mode_0 AS {run_mode_enum}), '
                f'CAST(:job_type_run_mode_1 AS {run_mode_enum}), '
                f'CAST(:job_type_run_mode_2 AS {run_mode_enum})'
                ')'
            )
            params['job_type_run_mode_0'] = BatchRunMode.FULL.value
            params['job_type_run_mode_1'] = BatchRunMode.PAGE_REBUILD.value
            params['job_type_run_mode_2'] = BatchRunMode.AI_RETRY.value

        where_sql = f'WHERE {" AND ".join(clauses)}' if clauses else ''
        return where_sql, params


__all__ = ['BatchJobRepository']
