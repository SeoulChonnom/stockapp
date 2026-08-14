"""Connect the theme catalog and archive search migration to Alembic.

Revision ID: 20260814_01_theme_archive_search
Revises: 20260810_01_step_errors
Create Date: 2026-08-14

The guarded SQL asset remains the single executable source for the legacy SQL
runner and startup Alembic. Alembic removes its standalone transaction guards
before executing the body in the revision transaction.
"""

from pathlib import Path

from alembic import op

revision = '20260814_01_theme_archive_search'
down_revision = '20260810_01_step_errors'
branch_labels = None
depends_on = None

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[2]
    / 'db'
    / 'migrations'
    / '20260813_08_theme_catalog_archive_search.sql'
)


def _load_migration_sql() -> str:
    sql = _MIGRATION_SQL.read_text(encoding='utf-8').strip()
    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
        raise RuntimeError(
            'The theme catalog migration has invalid transaction guards.'
        )
    return sql.removeprefix('BEGIN;').removesuffix('COMMIT;').strip()


def upgrade() -> None:
    migration_sql = _load_migration_sql()
    migration_context = op.get_context()
    if migration_context.as_sql:
        migration_context.impl.static_output(migration_sql)
        return
    op.execute(migration_sql)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS stock.market_daily_page_market_cluster_theme;
        DROP TABLE IF EXISTS stock.news_cluster_theme;
        DROP TABLE IF EXISTS stock.theme_catalog;
        ALTER TABLE stock.market_daily_page_market
            DROP COLUMN IF EXISTS search_document;
        ALTER TABLE stock.market_daily_page_market_cluster
            DROP COLUMN IF EXISTS search_document;
        """
    )
