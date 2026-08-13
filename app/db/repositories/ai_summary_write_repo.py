from __future__ import annotations

import json

from sqlalchemy import text

from app.batch.ai_summary_targets import build_ai_summary_target_key
from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import AiSummaryCreateParams, AiSummaryRecord


class AiSummaryWriteRepository(PostgresRepository):
    async def insert_summary(self, params: AiSummaryCreateParams) -> AiSummaryRecord:
        target_key = params.target_key or build_ai_summary_target_key(
            params.summary_type,
            market_type=params.market_type,
            cluster_id=params.cluster_id,
        )
        await self._lock_target(target_key)
        statement = text(
            """
            INSERT INTO {summary_table} (
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no
            )
            VALUES (
                :batch_job_id,
                CAST(:summary_type AS {summary_type_enum}),
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :cluster_id,
                :title,
                :body,
                CAST(:paragraphs_json AS JSONB),
                :model_name,
                :prompt_version,
                CAST(:status AS {status_enum}),
                :fallback_used,
                :error_message,
                CAST(:metadata_json AS JSONB),
                :target_key,
                :source_summary_id,
                (
                    SELECT COALESCE(MAX(existing.attempt_no), 0) + 1
                    FROM {summary_table} AS existing
                    WHERE existing.target_key = :target_key
                )
            )
            RETURNING
                id AS summary_id,
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            """.format(
                summary_table=qualify_db_identifier('ai_summary'),
                summary_type_enum=qualify_db_identifier('ai_summary_type_enum'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
                status_enum=qualify_db_identifier('ai_summary_status_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'batch_job_id': params.batch_job_id,
                'summary_type': params.summary_type,
                'business_date': params.business_date,
                'market_type': params.market_type,
                'cluster_id': params.cluster_id,
                'title': params.title,
                'body': params.body,
                'paragraphs_json': json.dumps(params.paragraphs_json),
                'model_name': params.model_name,
                'prompt_version': params.prompt_version,
                'status': params.status,
                'fallback_used': params.fallback_used,
                'error_message': params.error_message,
                'metadata_json': json.dumps(params.metadata_json),
                'target_key': target_key,
                'source_summary_id': params.source_summary_id,
                'attempt_no': params.attempt_no,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(AiSummaryRecord, row)

    async def upsert_full_run_summary(
        self, params: AiSummaryCreateParams
    ) -> AiSummaryRecord:
        """Persist a normal-batch target with resume-safe attempt lineage."""
        target_key = params.target_key or build_ai_summary_target_key(
            params.summary_type,
            market_type=params.market_type,
            cluster_id=params.cluster_id,
        )
        await self._lock_target(target_key)
        statement = text(
            """
            INSERT INTO {summary_table} (
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no
            )
            VALUES (
                :batch_job_id,
                CAST(:summary_type AS {summary_type_enum}),
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :cluster_id,
                :title,
                :body,
                CAST(:paragraphs_json AS JSONB),
                :model_name,
                :prompt_version,
                CAST(:status AS {status_enum}),
                :fallback_used,
                :error_message,
                CAST(:metadata_json AS JSONB),
                :target_key,
                :source_summary_id,
                (
                    SELECT COALESCE(MAX(existing.attempt_no), 0) + 1
                    FROM {summary_table} AS existing
                    WHERE existing.target_key = :target_key
                )
            )
            ON CONFLICT (batch_job_id, target_key) DO UPDATE
            SET
                title = EXCLUDED.title,
                body = EXCLUDED.body,
                paragraphs_json = EXCLUDED.paragraphs_json,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                status = EXCLUDED.status,
                fallback_used = EXCLUDED.fallback_used,
                error_message = EXCLUDED.error_message,
                metadata_json = EXCLUDED.metadata_json,
                generated_at = now()
            RETURNING
                id AS summary_id,
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            """.format(
                summary_table=qualify_db_identifier('ai_summary'),
                summary_type_enum=qualify_db_identifier('ai_summary_type_enum'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
                status_enum=qualify_db_identifier('ai_summary_status_enum'),
            )
        )
        result = await self.session.execute(
            statement,
            {
                'batch_job_id': params.batch_job_id,
                'summary_type': params.summary_type,
                'business_date': params.business_date,
                'market_type': params.market_type,
                'cluster_id': params.cluster_id,
                'title': params.title,
                'body': params.body,
                'paragraphs_json': json.dumps(params.paragraphs_json),
                'model_name': params.model_name,
                'prompt_version': params.prompt_version,
                'status': params.status,
                'fallback_used': params.fallback_used,
                'error_message': params.error_message,
                'metadata_json': json.dumps(params.metadata_json),
                'target_key': target_key,
                'source_summary_id': params.source_summary_id,
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(AiSummaryRecord, row)

    async def _lock_target(self, target_key: str) -> None:
        await self.session.execute(
            text(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(CAST(:target_key AS TEXT), 0)
                )
                """
            ),
            {'target_key': target_key},
        )

    async def upsert_retry_summary(
        self, params: AiSummaryCreateParams
    ) -> AiSummaryRecord:
        """Insert a retry result without ever replacing a successful result."""
        target_key = params.target_key or build_ai_summary_target_key(
            params.summary_type,
            market_type=params.market_type,
            cluster_id=params.cluster_id,
        )
        statement = text(
            """
            INSERT INTO {summary_table} (
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no
            )
            VALUES (
                :batch_job_id,
                CAST(:summary_type AS {summary_type_enum}),
                :business_date,
                CAST(:market_type AS {market_type_enum}),
                :cluster_id,
                :title,
                :body,
                CAST(:paragraphs_json AS JSONB),
                :model_name,
                :prompt_version,
                CAST(:status AS {status_enum}),
                :fallback_used,
                :error_message,
                CAST(:metadata_json AS JSONB),
                :target_key,
                :source_summary_id,
                :attempt_no
            )
            ON CONFLICT (batch_job_id, target_key) DO UPDATE
            SET
                title = EXCLUDED.title,
                body = EXCLUDED.body,
                paragraphs_json = EXCLUDED.paragraphs_json,
                model_name = EXCLUDED.model_name,
                prompt_version = EXCLUDED.prompt_version,
                status = EXCLUDED.status,
                fallback_used = EXCLUDED.fallback_used,
                error_message = EXCLUDED.error_message,
                metadata_json = EXCLUDED.metadata_json,
                generated_at = now()
            WHERE {summary_table}.status <> 'SUCCESS'
               OR {summary_table}.fallback_used
            RETURNING
                id AS summary_id,
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            """.format(
                summary_table=qualify_db_identifier('ai_summary'),
                summary_type_enum=qualify_db_identifier('ai_summary_type_enum'),
                market_type_enum=qualify_db_identifier('market_type_enum'),
                status_enum=qualify_db_identifier('ai_summary_status_enum'),
            )
        )
        params_dict = {
            'batch_job_id': params.batch_job_id,
            'summary_type': params.summary_type,
            'business_date': params.business_date,
            'market_type': params.market_type,
            'cluster_id': params.cluster_id,
            'title': params.title,
            'body': params.body,
            'paragraphs_json': json.dumps(params.paragraphs_json),
            'model_name': params.model_name,
            'prompt_version': params.prompt_version,
            'status': params.status,
            'fallback_used': params.fallback_used,
            'error_message': params.error_message,
            'metadata_json': json.dumps(params.metadata_json),
            'target_key': target_key,
            'source_summary_id': params.source_summary_id,
            'attempt_no': params.attempt_no,
        }
        result = await self.session.execute(statement, params_dict)
        row = result.mappings().one_or_none()
        if row is not None:
            return self._model_from_mapping(AiSummaryRecord, row)

        existing = await self.get_summary_for_job_target(
            params.batch_job_id, target_key
        )
        if existing is None:
            raise RuntimeError('AI retry upsert did not return or preserve a row.')
        return existing

    async def get_summary_for_job_target(
        self, job_id: int, target_key: str
    ) -> AiSummaryRecord | None:
        statement = text(
            """
            SELECT
                id AS summary_id,
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            FROM {summary_table}
            WHERE batch_job_id = :job_id
              AND target_key = :target_key
            """.format(summary_table=qualify_db_identifier('ai_summary'))
        )
        result = await self.session.execute(
            statement, {'job_id': job_id, 'target_key': target_key}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiSummaryRecord, row) if row else None

    async def list_summaries_for_job(self, job_id: int) -> list[AiSummaryRecord]:
        statement = text(
            """
            SELECT
                id AS summary_id,
                batch_job_id,
                summary_type,
                business_date,
                market_type,
                cluster_id,
                title,
                body,
                paragraphs_json,
                model_name,
                prompt_version,
                status,
                fallback_used,
                error_message,
                metadata_json,
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            FROM {summary_table}
            WHERE batch_job_id = :job_id
            ORDER BY generated_at ASC, id ASC
            """.format(summary_table=qualify_db_identifier('ai_summary'))
        )
        result = await self.session.execute(statement, {'job_id': job_id})
        return self._models_from_mappings(AiSummaryRecord, result.mappings().all())


__all__ = ['AiSummaryWriteRepository']
