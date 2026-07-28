BEGIN;

CREATE TABLE IF NOT EXISTS stock.batch_job_market_context (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL
        REFERENCES stock.batch_job(id) ON DELETE CASCADE,
    market_type stock.market_type_enum NOT NULL,
    expected_session_date DATE NOT NULL,
    actual_index_source_date DATE NULL,
    session_close_at TIMESTAMPTZ NOT NULL,
    news_window_start_at TIMESTAMPTZ NOT NULL,
    news_window_end_at TIMESTAMPTZ NOT NULL,
    news_coverage_complete BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_batch_job_market_context_job_market
        UNIQUE (batch_job_id, market_type),
    CONSTRAINT chk_batch_job_market_context_news_window
        CHECK (news_window_start_at <= news_window_end_at),
    CONSTRAINT chk_batch_job_market_context_source_not_future
        CHECK (
            actual_index_source_date IS NULL
            OR actual_index_source_date <= expected_session_date
        )
);

CREATE INDEX IF NOT EXISTS idx_batch_job_market_context_coverage
    ON stock.batch_job_market_context (
        market_type,
        news_window_end_at DESC
    )
    WHERE news_coverage_complete;

ALTER TABLE stock.market_index_daily
    ADD COLUMN IF NOT EXISTS source_date DATE NULL,
    ADD COLUMN IF NOT EXISTS expected_session_date DATE NULL,
    ADD COLUMN IF NOT EXISTS session_close_at TIMESTAMPTZ NULL;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_index_daily'::regclass
          AND conname = 'chk_market_index_daily_source_date_present'
    ) THEN
        ALTER TABLE stock.market_index_daily
            ADD CONSTRAINT chk_market_index_daily_source_date_present
            CHECK (source_date IS NOT NULL) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_index_daily'::regclass
          AND conname = 'chk_market_index_daily_expected_session_date_present'
    ) THEN
        ALTER TABLE stock.market_index_daily
            ADD CONSTRAINT
                chk_market_index_daily_expected_session_date_present
            CHECK (expected_session_date IS NOT NULL) NOT VALID;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_index_daily'::regclass
          AND conname = 'chk_market_index_daily_session_close_at_present'
    ) THEN
        ALTER TABLE stock.market_index_daily
            ADD CONSTRAINT chk_market_index_daily_session_close_at_present
            CHECK (session_close_at IS NOT NULL) NOT VALID;
    END IF;
END;
$migration$;

ALTER TABLE stock.market_daily_page_market
    ADD COLUMN IF NOT EXISTS expected_session_date DATE NULL,
    ADD COLUMN IF NOT EXISTS actual_index_source_date DATE NULL,
    ADD COLUMN IF NOT EXISTS session_close_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS news_window_start_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS news_window_end_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS news_coverage_complete BOOLEAN NULL;

ALTER TABLE stock.market_daily_page_market_index
    ADD COLUMN IF NOT EXISTS source_date DATE NULL,
    ADD COLUMN IF NOT EXISTS expected_session_date DATE NULL,
    ADD COLUMN IF NOT EXISTS session_close_at TIMESTAMPTZ NULL;

ALTER TABLE stock.news_article_raw
    DROP CONSTRAINT IF EXISTS uq_news_article_raw_provider_key;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_article_raw'::regclass
          AND conname = 'uq_news_article_raw_business_provider_key'
    ) THEN
        ALTER TABLE stock.news_article_raw
            ADD CONSTRAINT uq_news_article_raw_business_provider_key
            UNIQUE (business_date, provider_name, provider_article_key);
    END IF;
END;
$migration$;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_index_daily'::regclass
          AND conname = 'chk_market_index_daily_source_not_future'
    ) THEN
        ALTER TABLE stock.market_index_daily
            ADD CONSTRAINT chk_market_index_daily_source_not_future
            CHECK (
                source_date IS NULL
                OR expected_session_date IS NULL
                OR source_date <= expected_session_date
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_market'::regclass
          AND conname = 'chk_market_daily_page_market_news_window'
    ) THEN
        ALTER TABLE stock.market_daily_page_market
            ADD CONSTRAINT chk_market_daily_page_market_news_window
            CHECK (
                news_window_start_at IS NULL
                OR news_window_end_at IS NULL
                OR news_window_start_at <= news_window_end_at
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_market'::regclass
          AND conname = 'chk_market_daily_page_market_source_not_future'
    ) THEN
        ALTER TABLE stock.market_daily_page_market
            ADD CONSTRAINT chk_market_daily_page_market_source_not_future
            CHECK (
                actual_index_source_date IS NULL
                OR expected_session_date IS NULL
                OR actual_index_source_date <= expected_session_date
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_market_index'::regclass
          AND conname =
              'chk_market_daily_page_market_index_source_not_future'
    ) THEN
        ALTER TABLE stock.market_daily_page_market_index
            ADD CONSTRAINT
                chk_market_daily_page_market_index_source_not_future
            CHECK (
                source_date IS NULL
                OR expected_session_date IS NULL
                OR source_date <= expected_session_date
            );
    END IF;
END;
$migration$;

COMMIT;
