from __future__ import annotations

from sqlalchemy import text

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import AiSummaryRecord


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

    async def list_retry_lineage_summaries(
        self, source_job_id: int
    ) -> list[AiSummaryRecord]:
        """List immutable source summaries and every retry descendant."""
        statement = text(
            """
            WITH RECURSIVE lineage_jobs AS (
                SELECT
                    id,
                    ARRAY[id]::BIGINT[] AS path
                FROM {batch_job_table}
                WHERE id = :source_job_id

                UNION ALL

                SELECT
                    child.id,
                    lineage_jobs.path || child.id
                FROM lineage_jobs
                JOIN {batch_job_table} AS child
                  ON child.source_job_id = lineage_jobs.id
                WHERE child.run_mode IN ('PAGE_REBUILD', 'AI_RETRY')
                  AND NOT child.id = ANY(lineage_jobs.path)
                  AND cardinality(lineage_jobs.path) < 64
            )
            SELECT
                summary.id AS summary_id,
                summary.batch_job_id,
                summary.summary_type,
                summary.business_date,
                summary.market_type,
                summary.cluster_id,
                summary.title,
                summary.body,
                summary.paragraphs_json,
                summary.model_name,
                summary.prompt_version,
                summary.status,
                summary.fallback_used,
                summary.error_message,
                summary.metadata_json,
                summary.target_key,
                summary.source_summary_id,
                summary.attempt_no,
                summary.generated_at
            FROM {summary_table} AS summary
            JOIN lineage_jobs
              ON lineage_jobs.id = summary.batch_job_id
            ORDER BY
                summary.attempt_no ASC,
                summary.generated_at ASC,
                summary.id ASC
            """.format(
                summary_table=qualify_db_identifier('ai_summary'),
                batch_job_table=qualify_db_identifier('batch_job'),
            )
        )
        result = await self.session.execute(statement, {'source_job_id': source_job_id})
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
                target_key,
                source_summary_id,
                attempt_no,
                generated_at
            FROM {summary_table}
            WHERE cluster_id = :cluster_id
              AND summary_type = :summary_type
            ORDER BY
                CASE
                    WHEN status = 'SUCCESS' AND NOT fallback_used THEN 0
                    ELSE 1
                END,
                attempt_no DESC,
                generated_at DESC,
                id DESC
            LIMIT 1
            """.format(summary_table=qualify_db_identifier('ai_summary'))
        )
        result = await self.session.execute(
            statement, {'cluster_id': cluster_id, 'summary_type': summary_type}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiSummaryRecord, row) if row else None
