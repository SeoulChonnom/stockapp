"""Connect the page search-document migration to startup Alembic upgrades.

Revision ID: 20260814_02_page_search_document
Revises: 20260810_01_step_errors
Create Date: 2026-08-14

The SQL migration is retained as the single executable source for both the
legacy SQL runner and this Alembic revision.  Alembic strips its standalone
transaction guards before executing it inside the revision transaction.
"""

from pathlib import Path

from alembic import op

revision = '20260814_02_page_search_document'
down_revision = '20260810_01_step_errors'
branch_labels = None
depends_on = None

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[2]
    / 'db'
    / 'migrations'
    / '20260814_09_page_search_document.sql'
)


def _load_migration_sql() -> str:
    sql = _MIGRATION_SQL.read_text(encoding='utf-8').strip()
    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
        raise RuntimeError(
            'The page search migration has invalid transaction guards.'
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
    op.execute('DROP INDEX IF EXISTS stock.idx_market_daily_page_search_document')
    op.execute(
        'ALTER TABLE stock.market_daily_page '
        'DROP COLUMN IF EXISTS search_document'
    )
