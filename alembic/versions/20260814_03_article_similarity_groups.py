"""Connect the article-similarity groups migration to startup Alembic upgrades.

Revision ID: 20260814_03_article_similarity_groups
Revises: 20260814_02_page_search_document
Create Date: 2026-08-14

The guarded SQL asset remains the executable source for both the legacy SQL
runner and startup Alembic. Alembic removes its standalone transaction guards
before executing the body in the revision transaction.
"""

from pathlib import Path

from alembic import op

revision = '20260814_03_article_similarity_groups'
down_revision = '20260814_02_page_search_document'
branch_labels = None
depends_on = None

_MIGRATION_SQL = (
    Path(__file__).resolve().parents[2]
    / 'db'
    / 'migrations'
    / '20260813_09_article_similarity_groups.sql'
)


def _load_migration_sql() -> str:
    sql = _MIGRATION_SQL.read_text(encoding='utf-8').strip()
    if not sql.startswith('BEGIN;') or not sql.endswith('COMMIT;'):
        raise RuntimeError(
            'The article-similarity groups migration has invalid transaction guards.'
        )
    return sql.removeprefix('BEGIN;').removesuffix('COMMIT;').strip()


def upgrade() -> None:
    # The immutable baseline created Alembic's version column as VARCHAR(32),
    # while this descriptive revision identifier is longer than that legacy
    # limit. Widen it before Alembic records the new revision number.
    op.execute(
        'ALTER TABLE stock.alembic_version ALTER COLUMN version_num TYPE VARCHAR(128)'
    )
    migration_sql = _load_migration_sql()
    migration_context = op.get_context()
    if migration_context.as_sql:
        migration_context.impl.static_output(migration_sql)
        return
    op.get_bind().exec_driver_sql(migration_sql)


def downgrade() -> None:
    op.execute(
        """
        DROP TABLE IF EXISTS stock.news_cluster_similar_group_article;
        DROP TABLE IF EXISTS stock.news_cluster_similar_group;
        ALTER TABLE stock.market_daily_page_article_link
            DROP CONSTRAINT IF EXISTS
                chk_market_daily_page_article_link_exact_duplicate_count_non_negative;
        ALTER TABLE stock.market_daily_page_article_link
            DROP COLUMN IF EXISTS similar_group_rank,
            DROP COLUMN IF EXISTS is_similar_group_representative,
            DROP COLUMN IF EXISTS exact_duplicate_count;
        ALTER TABLE stock.market_daily_page_market_cluster
            DROP CONSTRAINT IF EXISTS
                chk_page_market_cluster_grouping_ready,
            DROP CONSTRAINT IF EXISTS
                chk_page_market_cluster_grouping_status,
            DROP COLUMN IF EXISTS article_grouping_status,
            DROP COLUMN IF EXISTS article_grouping_generated_at,
            DROP COLUMN IF EXISTS article_grouping_issue_code,
            DROP COLUMN IF EXISTS article_grouping_algorithm_version;
        ALTER TABLE stock.news_cluster
            DROP CONSTRAINT IF EXISTS chk_news_cluster_article_grouping_ready_generated,
            DROP CONSTRAINT IF EXISTS chk_news_cluster_article_grouping_status,
            DROP COLUMN IF EXISTS article_grouping_status,
            DROP COLUMN IF EXISTS article_grouping_generated_at,
            DROP COLUMN IF EXISTS article_grouping_issue_code;
        """
    )
