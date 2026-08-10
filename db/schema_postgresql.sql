BEGIN;

CREATE SCHEMA IF NOT EXISTS stock;
SET search_path TO stock, public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE market_type_enum AS ENUM ('US', 'KR');
CREATE TYPE page_status_enum AS ENUM ('READY', 'PARTIAL', 'FAILED');
CREATE TYPE batch_job_status_enum AS ENUM ('PENDING', 'RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED');
CREATE TYPE batch_run_mode_enum AS ENUM (
    'FULL',
    'PAGE_REBUILD',
    'AI_RETRY',
    'NEWS_COLLECTION'
);
CREATE TYPE batch_trigger_type_enum AS ENUM ('SCHEDULED', 'MANUAL', 'ADMIN_REBUILD');
CREATE TYPE ai_summary_status_enum AS ENUM ('SUCCESS', 'FAILED', 'FALLBACK');
CREATE TYPE ai_summary_type_enum AS ENUM (
    'GLOBAL_HEADLINE',
    'MARKET_SUMMARY',
    'CLUSTER_CARD_SUMMARY',
    'CLUSTER_DETAIL_ANALYSIS'
);
CREATE TYPE event_level_enum AS ENUM ('INFO', 'WARN', 'ERROR');
CREATE TYPE batch_step_status_enum AS ENUM ('RUNNING', 'SUCCEEDED', 'FAILED');

CREATE TABLE batch_job (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_name TEXT NOT NULL DEFAULT 'market_daily_batch',
    business_date DATE NOT NULL,
    status batch_job_status_enum NOT NULL,
    trigger_type batch_trigger_type_enum NOT NULL DEFAULT 'SCHEDULED',
    triggered_by_user_id TEXT NULL,
    force_run BOOLEAN NOT NULL DEFAULT FALSE,
    rebuild_page_only BOOLEAN NOT NULL DEFAULT FALSE,
    run_mode batch_run_mode_enum NOT NULL DEFAULT 'FULL',
    source_job_id BIGINT NULL
        CONSTRAINT fk_batch_job_source_job
        REFERENCES batch_job(id)
        ON DELETE SET NULL,
    source_page_id BIGINT NULL,
    idempotency_key TEXT NULL,
    market_scope VARCHAR(20) NOT NULL DEFAULT 'GLOBAL',
    queued_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    lease_owner TEXT NULL,
    lease_token UUID NULL,
    lease_expires_at TIMESTAMPTZ NULL,
    heartbeat_at TIMESTAMPTZ NULL,
    current_step TEXT NULL,
    checkpoint_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ NULL,
    duration_seconds INTEGER NULL,
    raw_news_count INTEGER NOT NULL DEFAULT 0,
    processed_news_count INTEGER NOT NULL DEFAULT 0,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    ai_target_count INTEGER NOT NULL DEFAULT 0,
    ai_attempted_count INTEGER NOT NULL DEFAULT 0,
    ai_success_count INTEGER NOT NULL DEFAULT 0,
    ai_fallback_count INTEGER NOT NULL DEFAULT 0,
    ai_failed_count INTEGER NOT NULL DEFAULT 0,
    ai_recovered_count INTEGER NOT NULL DEFAULT 0,
    page_id BIGINT NULL,
    page_version_no INTEGER NULL,
    partial_message TEXT NULL,
    error_code TEXT NULL,
    error_message TEXT NULL,
    log_summary TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_batch_job_duration_non_negative
        CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    CONSTRAINT chk_batch_job_counts_non_negative
        CHECK (
            raw_news_count >= 0
            AND processed_news_count >= 0
            AND cluster_count >= 0
        ),
    CONSTRAINT chk_batch_job_ai_counts_non_negative
        CHECK (
            ai_target_count >= 0
            AND ai_attempted_count >= 0
            AND ai_success_count >= 0
            AND ai_fallback_count >= 0
            AND ai_failed_count >= 0
            AND ai_recovered_count >= 0
        ),
    CONSTRAINT chk_batch_job_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_market_scope
        CHECK (market_scope = 'GLOBAL'),
    CONSTRAINT chk_batch_job_attempts
        CHECK (
            attempt_count >= 0
            AND max_attempts > 0
            AND attempt_count <= max_attempts
        ),
    CONSTRAINT chk_batch_job_idempotency_key
        CHECK (
            idempotency_key IS NULL
            OR length(btrim(idempotency_key)) BETWEEN 1 AND 200
        ),
    CONSTRAINT chk_batch_job_checkpoint_object
        CHECK (jsonb_typeof(checkpoint_json) = 'object')
);

CREATE UNIQUE INDEX uq_batch_job_one_active_market_daily_per_day
    ON batch_job (business_date)
    WHERE status IN ('PENDING', 'RUNNING')
      AND run_mode IN ('FULL', 'PAGE_REBUILD');

CREATE INDEX idx_batch_job_list
    ON batch_job (business_date DESC, started_at DESC);

CREATE INDEX idx_batch_job_status_started_at
    ON batch_job (status, started_at DESC);

CREATE INDEX idx_batch_job_page_id
    ON batch_job (page_id);

CREATE INDEX idx_batch_job_source_job_id
    ON batch_job (source_job_id);

CREATE INDEX idx_batch_job_source_page_id
    ON batch_job (source_page_id);

CREATE UNIQUE INDEX uq_batch_job_idempotency_key
    ON batch_job (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_batch_job_pending_claim
    ON batch_job (available_at, queued_at, id)
    WHERE status = 'PENDING';

CREATE INDEX idx_batch_job_expired_lease
    ON batch_job (lease_expires_at, id)
    WHERE status = 'RUNNING';

CREATE TABLE batch_job_market_context (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    market_type market_type_enum NOT NULL,
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

CREATE INDEX idx_batch_job_market_context_coverage
    ON batch_job_market_context (
        market_type,
        news_window_end_at DESC
    )
    WHERE news_coverage_complete;

CREATE TABLE batch_job_event (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    level event_level_enum NOT NULL,
    message TEXT NOT NULL,
    context_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_batch_job_event_job_created
    ON batch_job_event (batch_job_id, created_at);

CREATE TABLE batch_job_step_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status batch_step_status_enum NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ NULL,
    duration_ms INTEGER NULL,
    error_message TEXT NULL,
    error_log TEXT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_batch_job_step_run_job_seq UNIQUE (batch_job_id, seq),
    CONSTRAINT chk_batch_job_step_run_seq_positive
        CHECK (seq >= 1),
    CONSTRAINT chk_batch_job_step_run_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_step_run_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE TABLE news_search_keyword (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_name TEXT NOT NULL,
    market_type market_type_enum NOT NULL,
    keyword TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_news_search_keyword_keyword_not_blank
        CHECK (length(btrim(keyword)) > 0),
    CONSTRAINT chk_news_search_keyword_provider_name_not_blank
        CHECK (length(btrim(provider_name)) > 0),
    CONSTRAINT chk_news_search_keyword_priority_positive
        CHECK (priority > 0)
);

CREATE UNIQUE INDEX uq_news_search_keyword_provider_market_keyword_norm
    ON news_search_keyword (provider_name, market_type, lower(btrim(keyword)));

CREATE INDEX idx_news_search_keyword_active_priority
    ON news_search_keyword (provider_name, market_type, is_active, priority, id);

INSERT INTO news_search_keyword (
    provider_name,
    market_type,
    keyword,
    is_active,
    priority
)
VALUES
    ('NAVER_NEWS', 'US', '미국 증시', TRUE, 10),
    ('NAVER_NEWS', 'KR', '코스피', TRUE, 10)
ON CONFLICT DO NOTHING;

CREATE TABLE news_collection_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL
        REFERENCES batch_job(id) ON DELETE CASCADE,
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

CREATE INDEX idx_news_collection_run_window
    ON news_collection_run (provider_name, window_start_at, window_end_at);

CREATE TABLE news_collection_keyword_diagnostic (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    news_collection_run_id BIGINT NOT NULL
        REFERENCES news_collection_run(id) ON DELETE CASCADE,
    keyword_id BIGINT NOT NULL
        REFERENCES news_search_keyword(id) ON DELETE RESTRICT,
    provider_name TEXT NOT NULL,
    market_type market_type_enum NOT NULL,
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

CREATE INDEX idx_news_collection_keyword_diagnostic_market
    ON news_collection_keyword_diagnostic (
        news_collection_run_id,
        market_type,
        coverage_complete
    );

CREATE TABLE news_article_raw (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_name TEXT NOT NULL,
    provider_article_key TEXT NOT NULL,
    market_type market_type_enum NOT NULL,
    business_date DATE NULL,
    search_keyword TEXT NULL,
    title TEXT NOT NULL,
    publisher_name TEXT NULL,
    published_at TIMESTAMPTZ NULL,
    origin_link TEXT NULL,
    naver_link TEXT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_article_raw_provider_key
        UNIQUE (provider_name, provider_article_key)
);

CREATE INDEX idx_news_article_raw_market_published
    ON news_article_raw (market_type, published_at DESC);

CREATE TABLE news_article_raw_keyword_match (
    raw_article_id BIGINT NOT NULL
        REFERENCES news_article_raw(id) ON DELETE CASCADE,
    keyword_id BIGINT NOT NULL
        REFERENCES news_search_keyword(id) ON DELETE RESTRICT,
    market_type market_type_enum NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (raw_article_id, keyword_id)
);

CREATE INDEX idx_news_article_raw_keyword_match_market
    ON news_article_raw_keyword_match (market_type, raw_article_id);

CREATE TABLE news_article_processed (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_date DATE NOT NULL,
    market_type market_type_enum NOT NULL,
    dedupe_hash CHAR(64) NOT NULL,
    canonical_title TEXT NOT NULL,
    publisher_name TEXT NULL,
    published_at TIMESTAMPTZ NULL,
    origin_link TEXT NOT NULL,
    naver_link TEXT NULL,
    source_summary TEXT NULL,
    article_body_excerpt TEXT NULL,
    content_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_article_processed_business_date_dedupe_hash
        UNIQUE (business_date, market_type, dedupe_hash)
);

CREATE INDEX idx_news_article_processed_business_market
    ON news_article_processed (business_date, market_type, published_at DESC);

CREATE TABLE news_article_raw_processed_map (
    raw_article_id BIGINT NOT NULL REFERENCES news_article_raw(id) ON DELETE CASCADE,
    processed_article_id BIGINT NOT NULL REFERENCES news_article_processed(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (raw_article_id, processed_article_id)
);

CREATE INDEX idx_news_article_raw_processed_map_processed
    ON news_article_raw_processed_map (processed_article_id);

CREATE TABLE news_cluster (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_uid UUID NOT NULL DEFAULT gen_random_uuid(),
    business_date DATE NOT NULL,
    market_type market_type_enum NOT NULL,
    cluster_rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary_short TEXT NULL,
    summary_long TEXT NULL,
    analysis_paragraphs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    representative_article_id BIGINT NOT NULL,
    article_count INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_cluster_uid UNIQUE (cluster_uid),
    CONSTRAINT uq_news_cluster_rank UNIQUE (business_date, market_type, cluster_rank),
    CONSTRAINT chk_news_cluster_rank_positive CHECK (cluster_rank > 0),
    CONSTRAINT chk_news_cluster_article_count_non_negative CHECK (article_count >= 0)
);

CREATE INDEX idx_news_cluster_business_market
    ON news_cluster (business_date, market_type, cluster_rank);

CREATE TABLE news_cluster_article (
    cluster_id BIGINT NOT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    processed_article_id BIGINT NOT NULL REFERENCES news_article_processed(id) ON DELETE RESTRICT,
    article_rank INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cluster_id, processed_article_id),
    CONSTRAINT uq_news_cluster_article_rank UNIQUE (cluster_id, article_rank),
    CONSTRAINT chk_news_cluster_article_rank_positive CHECK (article_rank > 0)
);

CREATE INDEX idx_news_cluster_article_processed
    ON news_cluster_article (processed_article_id);

ALTER TABLE news_cluster
    ADD CONSTRAINT fk_news_cluster_representative_membership
    FOREIGN KEY (id, representative_article_id)
    REFERENCES news_cluster_article (cluster_id, processed_article_id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE market_index_daily (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_date DATE NOT NULL,
    market_type market_type_enum NOT NULL,
    source_date DATE NOT NULL,
    expected_session_date DATE NOT NULL,
    session_close_at TIMESTAMPTZ NOT NULL,
    index_code TEXT NOT NULL,
    index_name TEXT NOT NULL,
    close_price NUMERIC(20, 4) NOT NULL,
    change_value NUMERIC(20, 4) NOT NULL,
    change_percent NUMERIC(10, 4) NOT NULL,
    high_price NUMERIC(20, 4) NULL,
    low_price NUMERIC(20, 4) NULL,
    currency_code CHAR(3) NOT NULL,
    provider_name TEXT NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_index_daily UNIQUE (business_date, market_type, index_code),
    CONSTRAINT chk_market_index_daily_source_not_future
        CHECK (source_date <= expected_session_date)
);

CREATE INDEX idx_market_index_daily_business_market
    ON market_index_daily (business_date, market_type);

CREATE TABLE ai_summary (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    summary_type ai_summary_type_enum NOT NULL,
    business_date DATE NOT NULL,
    market_type market_type_enum NULL,
    cluster_id BIGINT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    title TEXT NULL,
    body TEXT NULL,
    paragraphs_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_name TEXT NULL,
    prompt_version TEXT NULL,
    status ai_summary_status_enum NOT NULL,
    fallback_used BOOLEAN NOT NULL DEFAULT FALSE,
    error_message TEXT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    target_key TEXT NOT NULL,
    source_summary_id BIGINT NULL
        CONSTRAINT fk_ai_summary_source_summary
        REFERENCES ai_summary(id) ON DELETE SET NULL,
    attempt_no INTEGER NOT NULL DEFAULT 1,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ai_summary_job_target UNIQUE (batch_job_id, target_key),
    CONSTRAINT chk_ai_summary_attempt_positive CHECK (attempt_no > 0),
    CONSTRAINT chk_ai_summary_target_key_not_blank
        CHECK (length(btrim(target_key)) > 0)
);

CREATE INDEX idx_ai_summary_lookup
    ON ai_summary (business_date, summary_type, market_type, generated_at DESC);

CREATE INDEX idx_ai_summary_cluster
    ON ai_summary (cluster_id);

CREATE INDEX idx_ai_summary_batch_job
    ON ai_summary (batch_job_id);

CREATE INDEX idx_ai_summary_source_summary
    ON ai_summary (source_summary_id);

CREATE INDEX idx_ai_summary_target_effective
    ON ai_summary (target_key, attempt_no DESC, generated_at DESC);

CREATE TABLE market_daily_page (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    business_date DATE NOT NULL,
    version_no INTEGER NOT NULL,
    page_title TEXT NOT NULL,
    status page_status_enum NOT NULL,
    global_headline TEXT NULL,
    partial_message TEXT NULL,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    raw_news_count INTEGER NOT NULL DEFAULT 0,
    processed_news_count INTEGER NOT NULL DEFAULT 0,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE RESTRICT,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_market_daily_page_business_version
        UNIQUE (business_date, version_no),
    CONSTRAINT chk_market_daily_page_version_positive
        CHECK (version_no > 0),
    CONSTRAINT chk_market_daily_page_counts_non_negative
        CHECK (
            raw_news_count >= 0
            AND processed_news_count >= 0
            AND cluster_count >= 0
        )
);

CREATE INDEX idx_market_daily_page_latest
    ON market_daily_page (business_date DESC, version_no DESC);

CREATE INDEX idx_market_daily_page_status_generated
    ON market_daily_page (status, generated_at DESC);

CREATE INDEX idx_market_daily_page_batch_job
    ON market_daily_page (batch_job_id);

CREATE INDEX idx_market_daily_page_archive_cover
    ON market_daily_page (business_date DESC, generated_at DESC)
    INCLUDE (id, page_title, global_headline, status, partial_message);

CREATE TABLE market_daily_page_market (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_id BIGINT NOT NULL REFERENCES market_daily_page(id) ON DELETE CASCADE,
    market_type market_type_enum NOT NULL,
    expected_session_date DATE NULL,
    actual_index_source_date DATE NULL,
    session_close_at TIMESTAMPTZ NULL,
    news_window_start_at TIMESTAMPTZ NULL,
    news_window_end_at TIMESTAMPTZ NULL,
    news_coverage_complete BOOLEAN NULL,
    display_order SMALLINT NOT NULL,
    market_label TEXT NOT NULL,
    summary_title TEXT NULL,
    summary_body TEXT NULL,
    analysis_background_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    analysis_key_themes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    analysis_outlook TEXT NULL,
    raw_news_count INTEGER NOT NULL DEFAULT 0,
    processed_news_count INTEGER NOT NULL DEFAULT 0,
    cluster_count INTEGER NOT NULL DEFAULT 0,
    last_updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    partial_message TEXT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT uq_market_daily_page_market_type UNIQUE (page_id, market_type),
    CONSTRAINT uq_market_daily_page_market_order UNIQUE (page_id, display_order),
    CONSTRAINT chk_market_daily_page_market_order_positive CHECK (display_order > 0),
    CONSTRAINT chk_market_daily_page_market_counts_non_negative
        CHECK (
            raw_news_count >= 0
            AND processed_news_count >= 0
            AND cluster_count >= 0
        ),
    CONSTRAINT chk_market_daily_page_market_news_window
        CHECK (
            news_window_start_at IS NULL
            OR news_window_end_at IS NULL
            OR news_window_start_at <= news_window_end_at
        ),
    CONSTRAINT chk_market_daily_page_market_source_not_future
        CHECK (
            actual_index_source_date IS NULL
            OR expected_session_date IS NULL
            OR actual_index_source_date <= expected_session_date
        )
);

CREATE INDEX idx_market_daily_page_market_page
    ON market_daily_page_market (page_id, display_order);

CREATE TABLE market_daily_page_market_index (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_market_id BIGINT NOT NULL REFERENCES market_daily_page_market(id) ON DELETE CASCADE,
    market_index_daily_id BIGINT NULL REFERENCES market_index_daily(id) ON DELETE SET NULL,
    source_date DATE NULL,
    expected_session_date DATE NULL,
    session_close_at TIMESTAMPTZ NULL,
    display_order SMALLINT NOT NULL,
    index_code TEXT NOT NULL,
    index_name TEXT NOT NULL,
    close_price NUMERIC(20, 4) NOT NULL,
    change_value NUMERIC(20, 4) NOT NULL,
    change_percent NUMERIC(10, 4) NOT NULL,
    high_price NUMERIC(20, 4) NULL,
    low_price NUMERIC(20, 4) NULL,
    currency_code CHAR(3) NOT NULL,
    CONSTRAINT uq_market_daily_page_market_index_order
        UNIQUE (page_market_id, display_order),
    CONSTRAINT chk_market_daily_page_market_index_order_positive
        CHECK (display_order > 0),
    CONSTRAINT chk_market_daily_page_market_index_source_not_future
        CHECK (
            source_date IS NULL
            OR expected_session_date IS NULL
            OR source_date <= expected_session_date
        )
);

CREATE INDEX idx_market_daily_page_market_index_market
    ON market_daily_page_market_index (page_market_id, display_order);

CREATE INDEX idx_market_daily_page_market_index_source
    ON market_daily_page_market_index (market_index_daily_id);

CREATE TABLE market_daily_page_market_cluster (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_market_id BIGINT NOT NULL REFERENCES market_daily_page_market(id) ON DELETE CASCADE,
    cluster_id BIGINT NULL REFERENCES news_cluster(id) ON DELETE SET NULL,
    cluster_uid UUID NOT NULL,
    display_order SMALLINT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NULL,
    article_count INTEGER NOT NULL DEFAULT 0,
    tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    representative_article_id BIGINT NULL REFERENCES news_article_processed(id) ON DELETE SET NULL,
    representative_title TEXT NULL,
    representative_publisher_name TEXT NULL,
    representative_published_at TIMESTAMPTZ NULL,
    representative_origin_link TEXT NULL,
    representative_naver_link TEXT NULL,
    CONSTRAINT uq_market_daily_page_market_cluster_order
        UNIQUE (page_market_id, display_order),
    CONSTRAINT chk_market_daily_page_market_cluster_order_positive
        CHECK (display_order > 0),
    CONSTRAINT chk_market_daily_page_market_cluster_article_count_non_negative
        CHECK (article_count >= 0)
);

CREATE INDEX idx_market_daily_page_market_cluster_market
    ON market_daily_page_market_cluster (page_market_id, display_order);

CREATE INDEX idx_market_daily_page_market_cluster_uid
    ON market_daily_page_market_cluster (cluster_uid);

CREATE INDEX idx_market_daily_page_market_cluster_cluster_id
    ON market_daily_page_market_cluster (cluster_id);

CREATE INDEX idx_market_daily_page_market_cluster_rep_article
    ON market_daily_page_market_cluster (representative_article_id);

CREATE TABLE market_daily_page_article_link (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    page_market_id BIGINT NOT NULL REFERENCES market_daily_page_market(id) ON DELETE CASCADE,
    display_order INTEGER NOT NULL,
    processed_article_id BIGINT NULL REFERENCES news_article_processed(id) ON DELETE SET NULL,
    cluster_id BIGINT NULL REFERENCES news_cluster(id) ON DELETE SET NULL,
    cluster_uid UUID NULL,
    cluster_title TEXT NULL,
    title TEXT NOT NULL,
    publisher_name TEXT NULL,
    published_at TIMESTAMPTZ NULL,
    origin_link TEXT NOT NULL,
    naver_link TEXT NULL,
    CONSTRAINT uq_market_daily_page_article_link_order
        UNIQUE (page_market_id, display_order),
    CONSTRAINT chk_market_daily_page_article_link_order_positive
        CHECK (display_order > 0)
);

CREATE INDEX idx_market_daily_page_article_link_market
    ON market_daily_page_article_link (page_market_id, display_order);

CREATE INDEX idx_market_daily_page_article_link_processed
    ON market_daily_page_article_link (processed_article_id);

CREATE INDEX idx_market_daily_page_article_link_cluster
    ON market_daily_page_article_link (cluster_id);

ALTER TABLE batch_job
    ADD CONSTRAINT fk_batch_job_page
    FOREIGN KEY (page_id) REFERENCES market_daily_page(id) ON DELETE SET NULL;

ALTER TABLE batch_job
    ADD CONSTRAINT fk_batch_job_source_page
    FOREIGN KEY (source_page_id) REFERENCES market_daily_page(id) ON DELETE SET NULL;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_batch_job_updated_at
BEFORE UPDATE ON batch_job
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_news_article_processed_updated_at
BEFORE UPDATE ON news_article_processed
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_news_cluster_updated_at
BEFORE UPDATE ON news_cluster
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMIT;
