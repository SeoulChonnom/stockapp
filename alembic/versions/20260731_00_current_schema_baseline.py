"""Establish the current schema after the seven legacy SQL migrations.

Revision ID: 20260731_00_baseline
Revises:
Create Date: 2026-07-31

The immutable SQL asset represents ``db/schema_postgresql.sql`` after:

- 20260728_01_batch_job_trigger_subject_text.sql
- 20260728_02_naver_news_keyword_seeds.sql
- 20260728_03_news_article_processed_date_dedupe.sql
- 20260729_04_batch_job_durable_queue.sql
- 20260729_05_market_session_context_source_date.sql
- 20260729_06_ai_summary_retry_lineage.sql
- 20260731_07_incremental_news_collection.sql
"""

from pathlib import Path

from alembic import op

revision = '20260731_00_baseline'
down_revision = None
branch_labels = None
depends_on = None

_BASELINE_SQL = (
    Path(__file__).resolve().parents[2]
    / 'db'
    / 'alembic'
    / 'baselines'
    / '20260731_schema.sql'
)


def _load_baseline_sql() -> str:
    sql = _BASELINE_SQL.read_text(encoding='utf-8').strip()
    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
        raise RuntimeError(
            'The immutable schema baseline has invalid transaction guards.'
        )
    return sql.removeprefix('BEGIN;').removesuffix('COMMIT;').strip()


def upgrade() -> None:
    baseline_sql = _load_baseline_sql()
    migration_context = op.get_context()
    if migration_context.as_sql:
        migration_context.impl.static_output(baseline_sql)
        return
    op.get_bind().exec_driver_sql(baseline_sql)


def downgrade() -> None:
    raise RuntimeError(
        'The current-schema baseline is irreversible; restore from backup instead.'
    )
