from __future__ import annotations

import json

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import AiSummaryCreateParams, AiSummaryRecord


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


class AiSummaryWriteRepository(PostgresRepository):
    async def insert_summary(self, params: AiSummaryCreateParams) -> AiSummaryRecord:
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
                metadata_json
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
                CAST(:metadata_json AS JSONB)
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
                generated_at
            """.format(
                summary_table=_qualified_table('ai_summary'),
                summary_type_enum=_qualified_table('ai_summary_type_enum'),
                market_type_enum=_qualified_table('market_type_enum'),
                status_enum=_qualified_table('ai_summary_status_enum'),
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
            },
        )
        row = result.mappings().one()
        return self._model_from_mapping(AiSummaryRecord, row)

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
                generated_at
            FROM {summary_table}
            WHERE batch_job_id = :job_id
            ORDER BY generated_at ASC, id ASC
            """.format(summary_table=_qualified_table('ai_summary'))
        )
        result = await self.session.execute(statement, {'job_id': job_id})
        return self._models_from_mappings(AiSummaryRecord, result.mappings().all())


__all__ = ['AiSummaryWriteRepository']
