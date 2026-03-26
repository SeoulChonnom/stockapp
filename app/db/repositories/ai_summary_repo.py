from __future__ import annotations

from sqlalchemy import text

from .base import PostgresRepository
from .projections import AiSummaryRecord


class AiSummaryRepository(PostgresRepository):
    async def get_latest_cluster_summary(
        self,
        cluster_id: int,
        *,
        summary_type: str = "CLUSTER_DETAIL_ANALYSIS",
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
            FROM ai_summary
            WHERE cluster_id = :cluster_id
              AND summary_type = :summary_type
            ORDER BY generated_at DESC, id DESC
            LIMIT 1
            """
        )
        result = await self.session.execute(
            statement, {"cluster_id": cluster_id, "summary_type": summary_type}
        )
        row = result.mappings().one_or_none()
        return self._model_from_mapping(AiSummaryRecord, row) if row else None
