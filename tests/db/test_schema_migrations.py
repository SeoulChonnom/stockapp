from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPOSITORY_ROOT / 'db' / 'schema_postgresql.sql'
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / 'db' / 'migrations'
THEME_MIGRATION = MIGRATIONS_DIRECTORY / '20260813_08_theme_catalog_archive_search.sql'
PAGE_SEARCH_MIGRATION = MIGRATIONS_DIRECTORY / '20260814_09_page_search_document.sql'

THEME_CODES = (
    'MACRO',
    'MACRO_ECONOMIC_DATA',
    'MACRO_ECONOMIC_DATA_INFLATION',
    'MACRO_ECONOMIC_DATA_EMPLOYMENT_GROWTH',
    'MACRO_MONETARY_MARKETS',
    'MACRO_MONETARY_MARKETS_INTEREST_RATES_BONDS',
    'MACRO_MONETARY_MARKETS_LIQUIDITY',
    'MACRO_MONETARY_MARKETS_FX',
    'MACRO_POLICY_RISK',
    'MACRO_POLICY_RISK_FISCAL_REGULATION',
    'MACRO_POLICY_RISK_GEOPOLITICS_TRADE',
    'SECTOR',
    'SECTOR_SEMICONDUCTORS',
    'SECTOR_SEMICONDUCTORS_MEMORY_HBM',
    'SECTOR_SEMICONDUCTORS_FOUNDRY_SYSTEM',
    'SECTOR_SEMICONDUCTORS_EQUIPMENT_MATERIALS',
    'SECTOR_AI_SOFTWARE',
    'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE',
    'SECTOR_AI_SOFTWARE_CLOUD_PLATFORM',
    'SECTOR_FINANCIALS',
    'SECTOR_FINANCIALS_BANKING',
    'SECTOR_FINANCIALS_SECURITIES_INSURANCE',
    'SECTOR_AUTOS_MOBILITY',
    'SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
    'SECTOR_AUTOS_MOBILITY_EV_BATTERY',
    'SECTOR_BIO_HEALTHCARE',
    'SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH',
    'SECTOR_BIO_HEALTHCARE_MEDICAL_SERVICES',
    'SECTOR_CONSUMER_CONTENT',
    'SECTOR_CONSUMER_CONTENT_RETAIL_ECOMMERCE',
    'SECTOR_CONSUMER_CONTENT_BRANDS_MEDIA_GAMING',
    'SECTOR_INDUSTRIALS_INFRA',
    'SECTOR_INDUSTRIALS_INFRA_SHIPBUILDING_DEFENSE',
    'SECTOR_INDUSTRIALS_INFRA_CONSTRUCTION_POWER',
    'SECTOR_INDUSTRIALS_INFRA_TRANSPORT_LOGISTICS',
    'SECTOR_ENERGY_MATERIALS',
    'SECTOR_ENERGY_MATERIALS_OIL_GAS',
    'SECTOR_ENERGY_MATERIALS_STEEL_CHEMICALS',
    'CORPORATE_EVENT',
    'CORPORATE_EVENT_PERFORMANCE',
    'CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE',
    'CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS',
    'CORPORATE_EVENT_CAPITAL_ACTION',
    'CORPORATE_EVENT_CAPITAL_ACTION_MNA',
    'CORPORATE_EVENT_CAPITAL_ACTION_IPO_CAPITAL_RAISE',
    'CORPORATE_EVENT_CAPITAL_ACTION_DIVIDEND_BUYBACK',
    'CORPORATE_EVENT_GOVERNANCE',
    'CORPORATE_EVENT_GOVERNANCE_MANAGEMENT',
    'MARKET_FLOW',
    'MARKET_FLOW_INVESTOR',
    'MARKET_FLOW_INVESTOR_FOREIGN',
    'MARKET_FLOW_INVESTOR_INSTITUTIONAL',
    'MARKET_FLOW_INVESTOR_RETAIL',
    'MARKET_FLOW_POSITIONING',
    'MARKET_FLOW_POSITIONING_SHORT_SELLING',
    'MARKET_FLOW_POSITIONING_ETF_REBALANCING',
    'MARKET_FLOW_POSITIONING_VOLATILITY_SENTIMENT',
    'ALTERNATIVE_ASSET',
    'ALTERNATIVE_ASSET_COMMODITIES',
    'ALTERNATIVE_ASSET_COMMODITIES_ENERGY_PRICES',
    'ALTERNATIVE_ASSET_COMMODITIES_METALS_AGRICULTURE',
    'ALTERNATIVE_ASSET_DIGITAL',
    'ALTERNATIVE_ASSET_DIGITAL_CRYPTO',
)


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


def test_processed_article_schema_dedupes_hash_within_business_date_and_market():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert re.search(
        r'CONSTRAINT uq_news_article_processed_business_date_dedupe_hash '
        r'UNIQUE \(business_date, market_type, dedupe_hash\)',
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

    assert 'NEWS_COLLECTION' in schema_sql
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


def test_incremental_news_schema_has_slot_diagnostics_and_global_raw_dedupe():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert 'CREATE TABLE news_collection_run' in schema_sql
    assert 'UNIQUE (provider_name, window_start_at, window_end_at)' in schema_sql
    assert 'CREATE TABLE news_collection_keyword_diagnostic' in schema_sql
    assert 'UNIQUE (provider_name, provider_article_key)' in schema_sql
    assert 'business_date DATE NULL' in schema_sql
    assert 'idx_news_article_raw_market_published' in schema_sql
    assert 'CREATE TABLE news_article_raw_keyword_match' in schema_sql


def test_incremental_news_migration_is_idempotent_and_decouples_market_jobs():
    migration_sql = _read_sql(
        MIGRATIONS_DIRECTORY / '20260731_07_incremental_news_collection.sql'
    )

    assert "ADD VALUE IF NOT EXISTS 'NEWS_COLLECTION'" in migration_sql
    assert 'CREATE TABLE IF NOT EXISTS stock.news_collection_run' in migration_sql
    assert (
        'CREATE TABLE IF NOT EXISTS '
        'stock.news_collection_keyword_diagnostic' in migration_sql
    )
    assert "run_mode IN ('FULL', 'PAGE_REBUILD')" in migration_sql
    assert 'PARTITION BY provider_name, provider_article_key' in migration_sql
    assert 'ALTER COLUMN business_date DROP NOT NULL' in migration_sql
    relationship_insert = migration_sql.index(
        'INSERT INTO stock.news_article_raw_keyword_match'
    )
    duplicate_delete = migration_sql.index('DELETE FROM stock.news_article_raw raw')
    assert relationship_insert < duplicate_delete
    assert 'UNIQUE (business_date, market_type, dedupe_hash)' in migration_sql


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


def test_theme_catalog_schema_has_hierarchy_constraints_and_canonical_seed():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert 'CREATE EXTENSION IF NOT EXISTS pg_trgm' in schema_sql
    assert 'CREATE TABLE theme_catalog' in schema_sql
    assert (
        'parent_code TEXT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT'
        in schema_sql
    )
    assert "code ~ '^[A-Z0-9_]+$'" in schema_sql
    assert 'code <> parent_code' in schema_sql
    assert 'UNIQUE (parent_code, sort_order)' in schema_sql
    assert 'CREATE TABLE news_cluster_theme' in schema_sql
    assert 'rank BETWEEN 1 AND 3' in schema_sql
    assert "classification_method IN ('LLM', 'KEYWORD_FALLBACK')" in schema_sql
    assert 'CREATE TABLE market_daily_page_market_cluster_theme' in schema_sql
    assert 'search_document TEXT NOT NULL DEFAULT' in schema_sql
    assert 'gin_trgm_ops' in schema_sql
    assert len(THEME_CODES) == 63
    for code in THEME_CODES:
        assert re.search(rf"\(\s*'{code}',", schema_sql)


def test_page_search_document_schema_is_normalized_and_indexed():
    schema_sql = _read_sql(SCHEMA_SQL)

    assert 'search_document TEXT NOT NULL DEFAULT' in schema_sql
    assert 'idx_market_daily_page_search_document' in schema_sql
    assert 'market_daily_page_search_document' in schema_sql


def test_page_search_document_migration_is_transactional_idempotent_and_backfills():
    assert PAGE_SEARCH_MIGRATION.exists()
    migration_sql = _read_sql(PAGE_SEARCH_MIGRATION)

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert (
        'ADD COLUMN IF NOT EXISTS search_document TEXT NOT NULL DEFAULT'
        in migration_sql
    )
    assert 'UPDATE stock.market_daily_page' in migration_sql
    assert 'page_title' in migration_sql
    assert 'global_headline' in migration_sql
    assert (
        'CREATE INDEX IF NOT EXISTS idx_market_daily_page_search_document'
        in migration_sql
    )


def test_theme_migration_is_transactional_qualified_and_idempotent():
    assert THEME_MIGRATION.exists()
    migration_sql = _read_sql(THEME_MIGRATION)

    assert migration_sql.startswith('BEGIN;')
    assert migration_sql.endswith('COMMIT;')
    assert 'CREATE EXTENSION IF NOT EXISTS pg_trgm' in migration_sql
    assert 'CREATE TABLE IF NOT EXISTS stock.theme_catalog' in migration_sql
    assert 'CREATE TABLE IF NOT EXISTS stock.news_cluster_theme' in migration_sql
    assert (
        'CREATE TABLE IF NOT EXISTS '
        'stock.market_daily_page_market_cluster_theme' in migration_sql
    )
    assert migration_sql.count('ADD COLUMN IF NOT EXISTS search_document') == 2
    assert 'ON CONFLICT (code) DO UPDATE' in migration_sql
    assert 'CREATE INDEX IF NOT EXISTS' in migration_sql
    for code in THEME_CODES:
        assert re.search(rf"\(\s*'{code}',", migration_sql)
