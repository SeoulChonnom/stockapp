from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPOSITORY_ROOT / 'db' / 'schema_postgresql.sql'
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / 'db' / 'migrations'


def _read_sql(path: Path) -> str:
    return re.sub(r'\s+', ' ', path.read_text(encoding='utf-8')).strip()


def test_canonical_schema_stores_arbitrary_auth_subjects_as_text():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert re.search(r'triggered_by_user_id TEXT NULL', schema_sql)
    assert not re.search(r'triggered_by_user_id UUID', schema_sql)


def test_auth_subject_migration_converts_legacy_uuid_column_idempotently():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260728_01_batch_job_trigger_subject_text.sql'
    )

    assert "data_type <> 'text'" in migration_sql
    assert (
        'ALTER COLUMN triggered_by_user_id TYPE TEXT USING triggered_by_user_id::text'
    ) in migration_sql


def test_fresh_schema_seeds_active_naver_news_keywords_for_both_markets():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert "('NAVER_NEWS', 'US', '미국 증시', TRUE, 10)" in schema_sql
    assert "('NAVER_NEWS', 'KR', '코스피', TRUE, 10)" in schema_sql


def test_keyword_migration_renames_legacy_provider_and_restores_seeds():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260728_02_naver_news_keyword_seeds.sql'
    )

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert "provider_name = 'NAVER_NEWS_SEARCH'" in migration_sql
    assert "SET provider_name = 'NAVER_NEWS'" in migration_sql
    assert "('NAVER_NEWS', 'US', '미국 증시', TRUE, 10)" in migration_sql
    assert "('NAVER_NEWS', 'KR', '코스피', TRUE, 10)" in migration_sql
    assert 'ON CONFLICT DO NOTHING' in migration_sql
    assert 'AND (NOT is_active OR priority > 10)' in migration_sql


def test_processed_article_schema_dedupes_hash_within_business_date():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert re.search(
        r'CONSTRAINT uq_news_article_processed_business_date_dedupe_hash '
        r'UNIQUE \(business_date, dedupe_hash\)',
        schema_sql,
    )
    assert not re.search(
        r'CONSTRAINT uq_news_article_processed_dedupe_hash '
        r'UNIQUE \(dedupe_hash\)',
        schema_sql,
    )


def test_processed_article_migration_replaces_global_unique_constraint():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260728_03_news_article_processed_date_dedupe.sql'
    )

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert 'CONCURRENTLY' not in migration_sql
    assert '(business_date, dedupe_hash)' in migration_sql
    assert (
        'DROP CONSTRAINT IF EXISTS uq_news_article_processed_dedupe_hash'
        in migration_sql
    )
    assert (
        'DROP INDEX IF EXISTS '
        'stock.uq_news_article_processed_business_date_dedupe_hash' in migration_sql
    )
    assert (
        'ADD CONSTRAINT uq_news_article_processed_business_date_dedupe_hash'
        in migration_sql
    )
    assert 'Duplicate processed articles prevent date-scoped dedupe' in migration_sql
