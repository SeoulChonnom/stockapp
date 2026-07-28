from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


@dataclass(frozen=True, slots=True)
class AiRetrySource:
    requested_job_id: int
    source_job_id: int
    source_page_id: int | None
    business_date: date
    source_status: str


@dataclass(frozen=True, slots=True)
class AiRetryJob:
    job_id: int
    job_name: str
    business_date: date
    status: str
    run_mode: str
    source_job_id: int
    source_page_id: int | None
    idempotency_key: str | None
    started_at: datetime
    checkpoint_json: dict | None = None
    page_id: int | None = None
    page_version_no: int | None = None
    ai_target_count: int = 0
    ai_attempted_count: int = 0
    ai_success_count: int = 0
    ai_fallback_count: int = 0
    ai_failed_count: int = 0
    ai_recovered_count: int = 0


@dataclass(frozen=True, slots=True)
class AiRetryEnqueueResult:
    job: AiRetryJob
    created: bool


class AiRetryIdempotencyConflictError(Exception):
    """Raised when one idempotency key identifies a different retry."""


class AiRetryEnqueuePort(Protocol):
    async def resolve_source(self, requested_job_id: int) -> AiRetrySource | None:
        """Resolve a requested job to the immutable root job and source page."""

    async def enqueue(
        self,
        *,
        source: AiRetrySource,
        triggered_by_user_id: str | None,
        idempotency_key: str | None,
    ) -> AiRetryEnqueueResult:
        """Create or return an idempotent PENDING AI retry job."""

    async def commit(self) -> None:
        """Commit the enqueue transaction."""


class PostgresAiRetryRepository(PostgresRepository):
    """Persistence adapter shared by the retry API and durable worker."""

    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()

    async def resolve_source(self, requested_job_id: int) -> AiRetrySource | None:
        statement = text(
            """
            SELECT
                requested.id AS requested_job_id,
                root.id AS source_job_id,
                COALESCE(
                    requested.page_id,
                    requested.source_page_id,
                    root.page_id
                ) AS source_page_id,
                root.business_date,
                root.status AS source_status
            FROM {batch_job_table} AS requested
            JOIN {batch_job_table} AS root
              ON root.id = COALESCE(requested.source_job_id, requested.id)
            WHERE requested.id = :requested_job_id
            """.format(batch_job_table=_qualified_table('batch_job'))
        )
        result = await self.session.execute(
            statement, {'requested_job_id': requested_job_id}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiRetrySource, row) if row else None

    async def get_job(self, job_id: int) -> AiRetryJob | None:
        statement = text(
            """
            SELECT
                id AS job_id,
                job_name,
                business_date,
                status,
                run_mode,
                source_job_id,
                source_page_id,
                idempotency_key,
                started_at,
                checkpoint_json,
                page_id,
                page_version_no,
                ai_target_count,
                ai_attempted_count,
                ai_success_count,
                ai_fallback_count,
                ai_failed_count,
                ai_recovered_count
            FROM {batch_job_table}
            WHERE id = :job_id
            """.format(batch_job_table=_qualified_table('batch_job'))
        )
        result = await self.session.execute(statement, {'job_id': job_id})
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiRetryJob, row) if row else None

    async def get_job_by_idempotency_key(
        self, idempotency_key: str
    ) -> AiRetryJob | None:
        statement = text(
            """
            SELECT
                id AS job_id,
                job_name,
                business_date,
                status,
                run_mode,
                source_job_id,
                source_page_id,
                idempotency_key,
                started_at,
                checkpoint_json,
                page_id,
                page_version_no,
                ai_target_count,
                ai_attempted_count,
                ai_success_count,
                ai_fallback_count,
                ai_failed_count,
                ai_recovered_count
            FROM {batch_job_table}
            WHERE idempotency_key = :idempotency_key
            """.format(batch_job_table=_qualified_table('batch_job'))
        )
        result = await self.session.execute(
            statement, {'idempotency_key': idempotency_key}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiRetryJob, row) if row else None

    async def enqueue(
        self,
        *,
        source: AiRetrySource,
        triggered_by_user_id: str | None,
        idempotency_key: str | None,
    ) -> AiRetryEnqueueResult:
        if idempotency_key:
            existing = await self.get_job_by_idempotency_key(idempotency_key)
            if existing is not None:
                self._validate_idempotent_job(existing, source)
                return AiRetryEnqueueResult(existing, created=False)

        statement = text(
            """
            INSERT INTO {batch_job_table} (
                business_date,
                status,
                trigger_type,
                triggered_by_user_id,
                force_run,
                rebuild_page_only,
                run_mode,
                source_job_id,
                source_page_id,
                idempotency_key
            )
            VALUES (
                :business_date,
                'PENDING',
                'ADMIN_REBUILD',
                CAST(:triggered_by_user_id AS TEXT),
                FALSE,
                FALSE,
                'AI_RETRY',
                :source_job_id,
                :source_page_id,
                :idempotency_key
            )
            RETURNING
                id AS job_id,
                job_name,
                business_date,
                status,
                run_mode,
                source_job_id,
                source_page_id,
                idempotency_key,
                started_at,
                checkpoint_json,
                page_id,
                page_version_no,
                ai_target_count,
                ai_attempted_count,
                ai_success_count,
                ai_fallback_count,
                ai_failed_count,
                ai_recovered_count
            """.format(batch_job_table=_qualified_table('batch_job'))
        )
        params = {
            'business_date': source.business_date,
            'triggered_by_user_id': triggered_by_user_id,
            'source_job_id': source.source_job_id,
            'source_page_id': source.source_page_id,
            'idempotency_key': idempotency_key,
        }
        try:
            result = await self.session.execute(statement, params)
        except IntegrityError:
            await self.rollback()
            if not idempotency_key:
                raise
            existing = await self.get_job_by_idempotency_key(idempotency_key)
            if existing is None:
                raise
            self._validate_idempotent_job(existing, source)
            return AiRetryEnqueueResult(existing, created=False)
        row = result.mappings().one()
        return AiRetryEnqueueResult(
            self._model_from_mapping(AiRetryJob, row), created=True
        )

    async def complete_job(
        self,
        *,
        job_id: int,
        status: str,
        counts: object,
        page_id: int | None,
        page_version_no: int | None,
        partial_message: str | None,
        log_summary: str | None,
        lease_token: UUID | None = None,
    ) -> bool:
        lease_predicate = ''
        if lease_token is not None:
            lease_predicate = (
                'AND lease_token = :lease_token '
                "AND status = 'RUNNING' AND lease_expires_at > now()"
            )
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
                page_id = :page_id,
                page_version_no = :page_version_no,
                partial_message = :partial_message,
                log_summary = :log_summary,
                ai_target_count = :ai_target_count,
                ai_attempted_count = :ai_attempted_count,
                ai_success_count = :ai_success_count,
                ai_fallback_count = :ai_fallback_count,
                ai_failed_count = :ai_failed_count,
                ai_recovered_count = :ai_recovered_count,
                lease_owner = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                updated_at = now()
            WHERE id = :job_id
            {lease_predicate}
            """.format(
                batch_job_table=_qualified_table('batch_job'),
                status_enum=_qualified_table('batch_job_status_enum'),
                lease_predicate=lease_predicate,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'status': status,
                'page_id': page_id,
                'page_version_no': page_version_no,
                'partial_message': partial_message,
                'log_summary': log_summary,
                'ai_target_count': counts.target_count,
                'ai_attempted_count': counts.attempted_count,
                'ai_success_count': counts.success_count,
                'ai_fallback_count': counts.fallback_count,
                'ai_failed_count': counts.failed_count,
                'ai_recovered_count': counts.recovered_count,
                'lease_token': lease_token,
            },
        )
        return bool(result.rowcount)

    @staticmethod
    def _validate_idempotent_job(existing: AiRetryJob, source: AiRetrySource) -> None:
        if (
            existing.run_mode != 'AI_RETRY'
            or existing.source_job_id != source.source_job_id
            or existing.business_date != source.business_date
        ):
            raise AiRetryIdempotencyConflictError


__all__ = [
    'AiRetryEnqueuePort',
    'AiRetryEnqueueResult',
    'AiRetryIdempotencyConflictError',
    'AiRetryJob',
    'AiRetrySource',
    'PostgresAiRetryRepository',
]
