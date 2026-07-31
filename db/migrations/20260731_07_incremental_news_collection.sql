BEGIN;

ALTER TYPE stock.batch_run_mode_enum
    ADD VALUE IF NOT EXISTS 'NEWS_COLLECTION';

COMMIT;

BEGIN;

DROP INDEX IF EXISTS stock.uq_batch_job_one_active_per_day;

CREATE UNIQUE INDEX IF NOT EXISTS
    uq_batch_job_one_active_market_daily_per_day
    ON stock.batch_job (business_date)
    WHERE status IN ('PENDING', 'RUNNING')
      AND run_mode IN ('FULL', 'PAGE_REBUILD');

CREATE TABLE IF NOT EXISTS stock.news_collection_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL
        REFERENCES stock.batch_job(id) ON DELETE CASCADE,
    provider_name TEXT NOT NULL,
    window_start_at TIMESTAMPTZ NOT NULL,
    window_end_at TIMESTAMPTZ NOT NULL,
    query_start_at TIMESTAMPTZ NOT NULL,
    query_end_at TIMESTAMPTZ NOT NULL,
    total_keyword_count INTEGER NOT NULL DEFAULT 0,
    completed_keyword_count INTEGER NOT NULL DEFAULT 0,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    coverage_complete BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_collection_run_batch_job UNIQUE (batch_job_id),
    CONSTRAINT uq_news_collection_run_provider_window
        UNIQUE (provider_name, window_start_at, window_end_at),
    CONSTRAINT chk_news_collection_run_window
        CHECK (window_start_at < window_end_at),
    CONSTRAINT chk_news_collection_run_query_window
        CHECK (
            query_start_at <= window_start_at
            AND query_end_at = window_end_at
        ),
    CONSTRAINT chk_news_collection_run_counts
        CHECK (
            total_keyword_count >= 0
            AND completed_keyword_count >= 0
            AND completed_keyword_count <= total_keyword_count
            AND fetched_count >= 0
            AND matched_count >= 0
            AND inserted_count >= 0
        )
);

CREATE INDEX IF NOT EXISTS idx_news_collection_run_window
    ON stock.news_collection_run (
        provider_name,
        window_start_at,
        window_end_at
    );

CREATE TABLE IF NOT EXISTS stock.news_collection_keyword_diagnostic (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    news_collection_run_id BIGINT NOT NULL
        REFERENCES stock.news_collection_run(id) ON DELETE CASCADE,
    keyword_id BIGINT NOT NULL
        REFERENCES stock.news_search_keyword(id) ON DELETE RESTRICT,
    provider_name TEXT NOT NULL,
    market_type stock.market_type_enum NOT NULL,
    keyword TEXT NOT NULL,
    status TEXT NOT NULL,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    matched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    coverage_complete BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT NULL,
    error_message TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_collection_keyword_diagnostic
        UNIQUE (news_collection_run_id, keyword_id),
    CONSTRAINT chk_news_collection_keyword_diagnostic_status
        CHECK (status IN ('SUCCESS', 'FAILED')),
    CONSTRAINT chk_news_collection_keyword_diagnostic_counts
        CHECK (
            fetched_count >= 0
            AND matched_count >= 0
            AND inserted_count >= 0
        )
);

CREATE INDEX IF NOT EXISTS idx_news_collection_keyword_diagnostic_market
    ON stock.news_collection_keyword_diagnostic (
        news_collection_run_id,
        market_type,
        coverage_complete
    );

CREATE TABLE IF NOT EXISTS stock.news_article_raw_keyword_match (
    raw_article_id BIGINT NOT NULL
        REFERENCES stock.news_article_raw(id) ON DELETE CASCADE,
    keyword_id BIGINT NOT NULL
        REFERENCES stock.news_search_keyword(id) ON DELETE RESTRICT,
    market_type stock.market_type_enum NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (raw_article_id, keyword_id)
);

CREATE INDEX IF NOT EXISTS idx_news_article_raw_keyword_match_market
    ON stock.news_article_raw_keyword_match (market_type, raw_article_id);

WITH ranked AS (
    SELECT
        id,
        min(id) OVER (
            PARTITION BY provider_name, provider_article_key
        ) AS keeper_id
    FROM stock.news_article_raw
)
INSERT INTO stock.news_article_raw_keyword_match (
    raw_article_id,
    keyword_id,
    market_type
)
SELECT
    ranked.keeper_id,
    keyword_match.keyword_id,
    keyword_match.market_type
FROM ranked
JOIN stock.news_article_raw_keyword_match keyword_match
  ON keyword_match.raw_article_id = ranked.id
ON CONFLICT DO NOTHING;

WITH ranked AS (
    SELECT
        id,
        min(id) OVER (
            PARTITION BY provider_name, provider_article_key
        ) AS keeper_id
    FROM stock.news_article_raw
)
INSERT INTO stock.news_article_raw_keyword_match (
    raw_article_id,
    keyword_id,
    market_type
)
SELECT
    ranked.keeper_id,
    keyword.id,
    raw.market_type
FROM ranked
JOIN stock.news_article_raw raw
  ON raw.id = ranked.id
JOIN stock.news_search_keyword keyword
  ON keyword.provider_name = raw.provider_name
 AND keyword.market_type = raw.market_type
 AND lower(btrim(keyword.keyword)) = lower(btrim(raw.search_keyword))
WHERE raw.search_keyword IS NOT NULL
ON CONFLICT DO NOTHING;

WITH ranked AS (
    SELECT
        id,
        min(id) OVER (
            PARTITION BY provider_name, provider_article_key
        ) AS keeper_id
    FROM stock.news_article_raw
),
duplicates AS (
    SELECT id, keeper_id
    FROM ranked
    WHERE id <> keeper_id
)
INSERT INTO stock.news_article_raw_processed_map (
    raw_article_id,
    processed_article_id
)
SELECT
    duplicates.keeper_id,
    mapping.processed_article_id
FROM duplicates
JOIN stock.news_article_raw_processed_map mapping
  ON mapping.raw_article_id = duplicates.id
ON CONFLICT DO NOTHING;

WITH ranked AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY provider_name, provider_article_key
            ORDER BY id
        ) AS row_number
    FROM stock.news_article_raw
)
DELETE FROM stock.news_article_raw raw
USING ranked
WHERE raw.id = ranked.id
  AND ranked.row_number > 1;

ALTER TABLE stock.news_article_raw
    DROP CONSTRAINT IF EXISTS uq_news_article_raw_business_provider_key,
    DROP CONSTRAINT IF EXISTS uq_news_article_raw_provider_key,
    ALTER COLUMN business_date DROP NOT NULL;

ALTER TABLE stock.news_article_raw
    ADD CONSTRAINT uq_news_article_raw_provider_key
    UNIQUE (provider_name, provider_article_key);

DROP INDEX IF EXISTS stock.idx_news_article_raw_business_market;

CREATE INDEX IF NOT EXISTS idx_news_article_raw_market_published
    ON stock.news_article_raw (market_type, published_at DESC);

ALTER TABLE stock.news_article_processed
    DROP CONSTRAINT IF EXISTS
        uq_news_article_processed_business_date_dedupe_hash;

ALTER TABLE stock.news_article_processed
    ADD CONSTRAINT uq_news_article_processed_business_date_dedupe_hash
    UNIQUE (business_date, market_type, dedupe_hash);

COMMIT;
