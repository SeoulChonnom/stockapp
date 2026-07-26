from __future__ import annotations

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import AiSummaryRecord


def _qualified_table(table_name: str) -> str:
    return qualify_db_identifier(table_name)


class AiSummaryRepository(PostgresRepository):
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

    async def get_latest_cluster_summary(
        self,
        cluster_id: int,
        *,
        summary_type: str = 'CLUSTER_DETAIL_ANALYSIS',
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
                generated_at
            FROM {summary_table}
            WHERE cluster_id = :cluster_id
              AND summary_type = :summary_type
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """.format(summary_table=_qualified_table('ai_summary'))
        )
        result = await self.session.execute(
            statement, {'cluster_id': cluster_id, 'summary_type': summary_type}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiSummaryRecord, row) if row else None
