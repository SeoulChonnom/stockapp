"""Backfill and pin the snapshot article-link similarity rank as NOT NULL.

Revision ID: 20260815_01_group_rank_not_null
Revises: 20260814_03_article_similarity_groups
Create Date: 2026-08-15

The guarded SQL asset remains the executable source for both the legacy SQL
runner and startup Alembic. Alembic removes its standalone transaction guards
before executing the body in the revision transaction.
"""

from pathlib import Path

from alembic import op

revision = '20260815_01_group_rank_not_null'
down_revision = '20260814_03_article_similarity_groups'
branch_labels = None
depends_on = None

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[2]
    / 'db'
    / 'migrations'
    / '20260815_10_article_link_group_rank_not_null.sql'
)


def _load_migration_sql() -> str:
    sql = _MIGRATION_SQL.read_text(encoding='utf-8').strip()
    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
        raise RuntimeError(
            'The article-link group rank migration has invalid transaction guards.'
        )
    return sql.removeprefix('BEGIN;').removesuffix('COMMIT;').strip()


def upgrade() -> None:
    migration_sql = _load_migration_sql()
    migration_context = op.get_context()
    if migration_context.as_sql:
        migration_context.impl.static_output(migration_sql)
        return
    op.get_bind().exec_driver_sql(migration_sql)


def downgrade() -> None:
    # Only the constraints this revision introduced are reverted. The
    # exact-duplicate check it repairs belongs to the canonical schema and to
    # 20260813_09's feature surface, so 20260814_03's downgrade owns it.
    op.execute(
        """
        ALTER TABLE stock.market_daily_page_article_link
            DROP CONSTRAINT IF EXISTS
                chk_market_daily_page_article_link_group_rank_positive;
        ALTER TABLE stock.market_daily_page_article_link
            ALTER COLUMN similar_group_rank DROP NOT NULL,
            ALTER COLUMN similar_group_rank DROP DEFAULT;
        """
    )
