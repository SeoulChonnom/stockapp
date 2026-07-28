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


def test_durable_queue_schema_has_claim_and_fencing_contract():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert (
        "CREATE TYPE batch_run_mode_enum AS ENUM ('FULL', 'PAGE_REBUILD', 'AI_RETRY')"
        in schema_sql
    )
    for column in (
        'idempotency_key TEXT NULL',
        'queued_at TIMESTAMPTZ NOT NULL DEFAULT now()',
        'available_at TIMESTAMPTZ NOT NULL DEFAULT now()',
        'lease_token UUID NULL',
        "checkpoint_json JSONB NOT NULL DEFAULT '{}'::jsonb",
    ):
        assert column in schema_sql
    assert "WHERE status = 'PENDING'" in schema_sql
    assert "WHERE status = 'RUNNING'" in schema_sql


def test_durable_queue_migration_is_idempotent_and_uses_partial_indexes():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260729_04_batch_job_durable_queue.sql'
    )

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert 'ADD COLUMN IF NOT EXISTS run_mode' in migration_sql
    assert (
        'CREATE UNIQUE INDEX IF NOT EXISTS uq_batch_job_idempotency_key'
        in migration_sql
    )
    assert 'WHERE idempotency_key IS NOT NULL' in migration_sql
    assert 'CREATE INDEX IF NOT EXISTS idx_batch_job_pending_claim' in migration_sql


def test_ai_retry_schema_has_target_lineage_and_typed_counts():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert 'target_key TEXT NOT NULL' in schema_sql
    assert 'source_summary_id BIGINT NULL' in schema_sql
    assert 'attempt_no INTEGER NOT NULL DEFAULT 1' in schema_sql
    assert (
        'CONSTRAINT uq_ai_summary_job_target '
        'UNIQUE (batch_job_id, target_key)' in schema_sql
    )
    for column in (
        'ai_target_count',
        'ai_attempted_count',
        'ai_success_count',
        'ai_fallback_count',
        'ai_failed_count',
        'ai_recovered_count',
    ):
        assert f'{column} INTEGER NOT NULL DEFAULT 0' in schema_sql


def test_ai_retry_lineage_migration_is_transactional_and_idempotent():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260729_06_ai_summary_retry_lineage.sql'
    )

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert 'ADD COLUMN IF NOT EXISTS target_key TEXT NULL' in migration_sql
    assert "'MARKET_SUMMARY:' || market_type::TEXT" in migration_sql
    assert 'ADD CONSTRAINT uq_ai_summary_job_target' in migration_sql
    assert 'UNIQUE (batch_job_id, target_key)' in migration_sql
    assert 'FOREIGN KEY (source_summary_id)' in migration_sql
    assert 'CREATE INDEX IF NOT EXISTS idx_ai_summary_target_effective' in migration_sql


def test_market_session_migration_preserves_legacy_nulls_but_rejects_new_nulls():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260729_05_market_session_context_source_date.sql'
    )

    for constraint_name in (
        'chk_market_index_daily_source_date_present',
        'chk_market_index_daily_expected_session_date_present',
        'chk_market_index_daily_session_close_at_present',
    ):
        assert constraint_name in migration_sql
    assert migration_sql.count('NOT VALID') == 3
