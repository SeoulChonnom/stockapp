BEGIN;

CREATE SCHEMA IF NOT EXISTS stock;
SET search_path TO stock, public;

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
    article_grouping_status TEXT NOT NULL DEFAULT 'UNAVAILABLE',
    article_grouping_generated_at TIMESTAMPTZ NULL,
    article_grouping_issue_code TEXT NULL
        DEFAULT 'SIMILARITY_GROUPING_FAILED',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_cluster_uid UNIQUE (cluster_uid),
    CONSTRAINT uq_news_cluster_rank UNIQUE (business_date, market_type, cluster_rank),
    CONSTRAINT chk_news_cluster_rank_positive CHECK (cluster_rank > 0),
    CONSTRAINT chk_news_cluster_article_count_non_negative CHECK (article_count >= 0),
    CONSTRAINT chk_news_cluster_article_grouping_status
        CHECK (article_grouping_status IN ('READY', 'UNAVAILABLE')),
    CONSTRAINT chk_news_cluster_article_grouping_ready_generated
        CHECK (
            (
                article_grouping_status = 'READY'
                AND article_grouping_generated_at IS NOT NULL
                AND article_grouping_issue_code IS NULL
            )
            OR (
                article_grouping_status = 'UNAVAILABLE'
                AND article_grouping_generated_at IS NULL
                AND article_grouping_issue_code = 'SIMILARITY_GROUPING_FAILED'
            )
        )
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

CREATE TABLE news_cluster_similar_group (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_id BIGINT NOT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    group_rank SMALLINT NOT NULL,
    representative_article_id BIGINT NOT NULL,
    algorithm_version TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT uq_news_cluster_similar_group_cluster_rank
        UNIQUE (cluster_id, group_rank),
    CONSTRAINT chk_similar_group_rank_positive
        CHECK (group_rank > 0),
    CONSTRAINT fk_news_cluster_similar_group_representative_membership
        FOREIGN KEY (cluster_id, representative_article_id)
        REFERENCES news_cluster_article (cluster_id, processed_article_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE news_cluster_similar_group_article (
    similar_group_id BIGINT NOT NULL
        REFERENCES news_cluster_similar_group(id) ON DELETE CASCADE,
    processed_article_id BIGINT NOT NULL
        REFERENCES news_article_processed(id) ON DELETE RESTRICT,
    similarity_score DOUBLE PRECISION NOT NULL,
    exact_duplicate_count INTEGER NOT NULL,
    is_representative BOOLEAN NOT NULL,
    article_rank SMALLINT NOT NULL,
    CONSTRAINT chk_similar_group_article_exact_count_non_negative
        CHECK (exact_duplicate_count >= 0),
    CONSTRAINT chk_similar_group_article_rank_positive
        CHECK (article_rank > 0),
    PRIMARY KEY (similar_group_id, processed_article_id),
    CONSTRAINT uq_news_cluster_similar_group_article_group_rank
        UNIQUE (similar_group_id, article_rank)
);

CREATE INDEX idx_news_cluster_similar_group_article_processed
    ON news_cluster_similar_group_article (processed_article_id);

CREATE TABLE theme_catalog (
    code TEXT PRIMARY KEY,
    parent_code TEXT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    sort_order SMALLINT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_theme_catalog_parent_sort_order
        UNIQUE (parent_code, sort_order),
    CONSTRAINT chk_theme_catalog_code_format
        CHECK (code ~ '^[A-Z0-9_]+$'),
    CONSTRAINT chk_theme_catalog_not_self_parent
        CHECK (parent_code IS NULL OR code <> parent_code)
);

CREATE INDEX idx_theme_catalog_parent_sort_order
    ON theme_catalog (parent_code, sort_order);

INSERT INTO theme_catalog (
    code,
    parent_code,
    label,
    description,
    sort_order,
    is_active
)
VALUES
    ('MACRO', NULL, '거시경제', '경제 전반과 금융시장의 거시 흐름', 1, TRUE),
    (
        'MACRO_ECONOMIC_DATA', 'MACRO', '경제지표',
        '물가와 고용 등 주요 경제지표', 1, TRUE
    ),
    (
        'MACRO_ECONOMIC_DATA_INFLATION', 'MACRO_ECONOMIC_DATA', '인플레이션',
        '물가 상승과 인플레이션 흐름', 1, TRUE
    ),
    (
        'MACRO_ECONOMIC_DATA_EMPLOYMENT_GROWTH', 'MACRO_ECONOMIC_DATA',
        '고용·성장', '고용과 경제성장 관련 지표', 2, TRUE
    ),
    (
        'MACRO_MONETARY_MARKETS', 'MACRO', '통화·금융시장',
        '금리와 유동성 및 환율 등 통화금융 변수', 2, TRUE
    ),
    (
        'MACRO_MONETARY_MARKETS_INTEREST_RATES_BONDS',
        'MACRO_MONETARY_MARKETS', '금리·채권', '금리와 채권시장 흐름', 1, TRUE
    ),
    (
        'MACRO_MONETARY_MARKETS_LIQUIDITY', 'MACRO_MONETARY_MARKETS', '유동성',
        '시장 유동성과 자금 흐름', 2, TRUE
    ),
    (
        'MACRO_MONETARY_MARKETS_FX', 'MACRO_MONETARY_MARKETS', '환율',
        '주요 통화의 환율과 외환시장 흐름', 3, TRUE
    ),
    (
        'MACRO_POLICY_RISK', 'MACRO', '정책·리스크',
        '재정과 규제 및 지정학·무역 정책 위험', 3, TRUE
    ),
    (
        'MACRO_POLICY_RISK_FISCAL_REGULATION', 'MACRO_POLICY_RISK',
        '재정·규제', '재정정책과 산업 규제 변화', 1, TRUE
    ),
    (
        'MACRO_POLICY_RISK_GEOPOLITICS_TRADE', 'MACRO_POLICY_RISK',
        '지정학·무역', '지정학적 갈등과 무역정책 변화', 2, TRUE
    ),
    ('SECTOR', NULL, '산업·섹터', '기업이 속한 산업과 섹터별 이슈', 2, TRUE),
    (
        'SECTOR_SEMICONDUCTORS', 'SECTOR', '반도체',
        '메모리와 시스템 반도체 산업 이슈', 1, TRUE
    ),
    (
        'SECTOR_SEMICONDUCTORS_MEMORY_HBM', 'SECTOR_SEMICONDUCTORS',
        '메모리·HBM', '메모리 반도체와 HBM 관련 이슈', 1, TRUE
    ),
    (
        'SECTOR_SEMICONDUCTORS_FOUNDRY_SYSTEM', 'SECTOR_SEMICONDUCTORS',
        '파운드리·시스템', '파운드리와 시스템 반도체 관련 이슈', 2, TRUE
    ),
    (
        'SECTOR_SEMICONDUCTORS_EQUIPMENT_MATERIALS', 'SECTOR_SEMICONDUCTORS',
        '장비·소재', '반도체 장비와 소재 관련 이슈', 3, TRUE
    ),
    (
        'SECTOR_AI_SOFTWARE', 'SECTOR', 'AI·소프트웨어',
        '인공지능과 소프트웨어 산업 이슈', 2, TRUE
    ),
    (
        'SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE', 'SECTOR_AI_SOFTWARE',
        'AI 인프라', 'AI 연산과 데이터센터 인프라 이슈', 1, TRUE
    ),
    (
        'SECTOR_AI_SOFTWARE_CLOUD_PLATFORM', 'SECTOR_AI_SOFTWARE',
        '클라우드·플랫폼', '클라우드와 플랫폼 사업 이슈', 2, TRUE
    ),
    (
        'SECTOR_FINANCIALS', 'SECTOR', '금융',
        '은행과 증권·보험 산업 이슈', 3, TRUE
    ),
    (
        'SECTOR_FINANCIALS_BANKING', 'SECTOR_FINANCIALS', '은행',
        '은행업과 예대금리 관련 이슈', 1, TRUE
    ),
    (
        'SECTOR_FINANCIALS_SECURITIES_INSURANCE', 'SECTOR_FINANCIALS',
        '증권·보험', '증권업과 보험업 관련 이슈', 2, TRUE
    ),
    (
        'SECTOR_AUTOS_MOBILITY', 'SECTOR', '자동차·모빌리티',
        '자동차와 모빌리티 산업 이슈', 4, TRUE
    ),
    (
        'SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS',
        'SECTOR_AUTOS_MOBILITY', '완성차·부품', '완성차와 자동차 부품 이슈', 1, TRUE
    ),
    (
        'SECTOR_AUTOS_MOBILITY_EV_BATTERY', 'SECTOR_AUTOS_MOBILITY',
        '전기차·배터리', '전기차와 배터리 산업 이슈', 2, TRUE
    ),
    (
        'SECTOR_BIO_HEALTHCARE', 'SECTOR', '바이오·헬스케어',
        '제약·바이오와 의료 서비스 산업 이슈', 5, TRUE
    ),
    (
        'SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH', 'SECTOR_BIO_HEALTHCARE',
        '제약·바이오', '제약과 바이오테크 산업 이슈', 1, TRUE
    ),
    (
        'SECTOR_BIO_HEALTHCARE_MEDICAL_SERVICES', 'SECTOR_BIO_HEALTHCARE',
        '의료 서비스', '병원과 의료 서비스 산업 이슈', 2, TRUE
    ),
    (
        'SECTOR_CONSUMER_CONTENT', 'SECTOR', '소비재·콘텐츠',
        '유통·이커머스와 브랜드·미디어·게임 이슈', 6, TRUE
    ),
    (
        'SECTOR_CONSUMER_CONTENT_RETAIL_ECOMMERCE', 'SECTOR_CONSUMER_CONTENT',
        '유통·이커머스', '유통과 전자상거래 산업 이슈', 1, TRUE
    ),
    (
        'SECTOR_CONSUMER_CONTENT_BRANDS_MEDIA_GAMING',
        'SECTOR_CONSUMER_CONTENT', '브랜드·미디어·게임',
        '소비자 브랜드와 미디어·게임 산업 이슈', 2, TRUE
    ),
    (
        'SECTOR_INDUSTRIALS_INFRA', 'SECTOR', '산업재·인프라',
        '조선·방산과 건설·전력 및 운송·물류 이슈', 7, TRUE
    ),
    (
        'SECTOR_INDUSTRIALS_INFRA_SHIPBUILDING_DEFENSE',
        'SECTOR_INDUSTRIALS_INFRA', '조선·방산', '조선업과 방위산업 이슈', 1, TRUE
    ),
    (
        'SECTOR_INDUSTRIALS_INFRA_CONSTRUCTION_POWER',
        'SECTOR_INDUSTRIALS_INFRA', '건설·전력', '건설업과 전력 인프라 이슈', 2, TRUE
    ),
    (
        'SECTOR_INDUSTRIALS_INFRA_TRANSPORT_LOGISTICS',
        'SECTOR_INDUSTRIALS_INFRA', '운송·물류', '운송과 물류 산업 이슈', 3, TRUE
    ),
    (
        'SECTOR_ENERGY_MATERIALS', 'SECTOR', '에너지·소재',
        '석유·가스와 철강·화학 산업 이슈', 8, TRUE
    ),
    (
        'SECTOR_ENERGY_MATERIALS_OIL_GAS', 'SECTOR_ENERGY_MATERIALS',
        '석유·가스', '석유와 천연가스 산업 이슈', 1, TRUE
    ),
    (
        'SECTOR_ENERGY_MATERIALS_STEEL_CHEMICALS', 'SECTOR_ENERGY_MATERIALS',
        '철강·화학', '철강과 화학 산업 이슈', 2, TRUE
    ),
    (
        'CORPORATE_EVENT', NULL, '기업 이벤트',
        '기업 실적과 자본·경영 이벤트', 3, TRUE
    ),
    (
        'CORPORATE_EVENT_PERFORMANCE', 'CORPORATE_EVENT', '실적·가이던스',
        '기업 실적 발표와 전망 변경', 1, TRUE
    ),
    (
        'CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE',
        'CORPORATE_EVENT_PERFORMANCE', '실적·가이던스',
        '분기 실적과 실적 전망 관련 이벤트', 1, TRUE
    ),
    (
        'CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS',
        'CORPORATE_EVENT_PERFORMANCE', '수주·계약',
        '수주와 공급계약 관련 이벤트', 2, TRUE
    ),
    (
        'CORPORATE_EVENT_CAPITAL_ACTION', 'CORPORATE_EVENT', '자본행사',
        '인수·합병과 자금조달 및 주주환원 이벤트', 2, TRUE
    ),
    (
        'CORPORATE_EVENT_CAPITAL_ACTION_MNA', 'CORPORATE_EVENT_CAPITAL_ACTION',
        '인수·합병', '기업 인수와 합병 관련 이벤트', 1, TRUE
    ),
    (
        'CORPORATE_EVENT_CAPITAL_ACTION_IPO_CAPITAL_RAISE',
        'CORPORATE_EVENT_CAPITAL_ACTION', 'IPO·자금조달',
        '기업공개와 신규 자금조달 이벤트', 2, TRUE
    ),
    (
        'CORPORATE_EVENT_CAPITAL_ACTION_DIVIDEND_BUYBACK',
        'CORPORATE_EVENT_CAPITAL_ACTION', '배당·자사주 매입',
        '배당과 자사주 매입 등 주주환원 이벤트', 3, TRUE
    ),
    (
        'CORPORATE_EVENT_GOVERNANCE', 'CORPORATE_EVENT', '지배구조',
        '경영진과 기업 지배구조 변화', 3, TRUE
    ),
    (
        'CORPORATE_EVENT_GOVERNANCE_MANAGEMENT', 'CORPORATE_EVENT_GOVERNANCE',
        '경영진', '대표와 주요 경영진 관련 이벤트', 1, TRUE
    ),
    ('MARKET_FLOW', NULL, '시장 수급', '투자자별 수급과 포지셔닝 흐름', 4, TRUE),
    (
        'MARKET_FLOW_INVESTOR', 'MARKET_FLOW', '투자자 수급',
        '투자자 유형별 매매와 수급 흐름', 1, TRUE
    ),
    (
        'MARKET_FLOW_INVESTOR_FOREIGN', 'MARKET_FLOW_INVESTOR', '외국인',
        '외국인 투자자의 매매와 수급', 1, TRUE
    ),
    (
        'MARKET_FLOW_INVESTOR_INSTITUTIONAL', 'MARKET_FLOW_INVESTOR', '기관',
        '기관 투자자의 매매와 수급', 2, TRUE
    ),
    (
        'MARKET_FLOW_INVESTOR_RETAIL', 'MARKET_FLOW_INVESTOR', '개인',
        '개인 투자자의 매매와 수급', 3, TRUE
    ),
    (
        'MARKET_FLOW_POSITIONING', 'MARKET_FLOW', '포지셔닝',
        '공매도·ETF·변동성 등 시장 포지셔닝', 2, TRUE
    ),
    (
        'MARKET_FLOW_POSITIONING_SHORT_SELLING', 'MARKET_FLOW_POSITIONING',
        '공매도', '공매도 잔고와 거래 흐름', 1, TRUE
    ),
    (
        'MARKET_FLOW_POSITIONING_ETF_REBALANCING', 'MARKET_FLOW_POSITIONING',
        'ETF 리밸런싱', 'ETF 편입과 리밸런싱 수급', 2, TRUE
    ),
    (
        'MARKET_FLOW_POSITIONING_VOLATILITY_SENTIMENT', 'MARKET_FLOW_POSITIONING',
        '변동성·심리', '시장 변동성과 투자심리 흐름', 3, TRUE
    ),
    (
        'ALTERNATIVE_ASSET', NULL, '대체자산',
        '원자재와 디지털 자산 시장', 5, TRUE
    ),
    (
        'ALTERNATIVE_ASSET_COMMODITIES', 'ALTERNATIVE_ASSET', '원자재',
        '에너지와 금속·농산물 원자재 시장', 1, TRUE
    ),
    (
        'ALTERNATIVE_ASSET_COMMODITIES_ENERGY_PRICES',
        'ALTERNATIVE_ASSET_COMMODITIES', '에너지 가격',
        '원유와 천연가스 등 에너지 가격', 1, TRUE
    ),
    (
        'ALTERNATIVE_ASSET_COMMODITIES_METALS_AGRICULTURE',
        'ALTERNATIVE_ASSET_COMMODITIES', '금속·농산물',
        '금속과 농산물 원자재 가격', 2, TRUE
    ),
    (
        'ALTERNATIVE_ASSET_DIGITAL', 'ALTERNATIVE_ASSET', '디지털 자산',
        '가상자산 등 디지털 자산 시장', 2, TRUE
    ),
    (
        'ALTERNATIVE_ASSET_DIGITAL_CRYPTO', 'ALTERNATIVE_ASSET_DIGITAL',
        '가상자산', '비트코인 등 가상자산 시장', 1, TRUE
    )
ON CONFLICT (code) DO UPDATE SET
    parent_code = EXCLUDED.parent_code,
    label = EXCLUDED.label,
    description = EXCLUDED.description,
    sort_order = EXCLUDED.sort_order,
    is_active = EXCLUDED.is_active,
    updated_at = now();

DO $theme_catalog_validation$
DECLARE
    cycle_code TEXT;
BEGIN
    WITH RECURSIVE lineage AS (
        SELECT
            code,
            parent_code,
            ARRAY[code]::TEXT[] AS path,
            FALSE AS has_cycle
        FROM theme_catalog
        UNION ALL
        SELECT
            parent.code,
            parent.parent_code,
            lineage.path || parent.code,
            parent.code = ANY(lineage.path)
        FROM lineage
        JOIN theme_catalog AS parent
          ON parent.code = lineage.parent_code
        WHERE NOT lineage.has_cycle
    )
    SELECT code
    INTO cycle_code
    FROM lineage
    WHERE has_cycle
    LIMIT 1;

    IF cycle_code IS NOT NULL THEN
        RAISE EXCEPTION 'Theme catalog contains a parent cycle at %.', cycle_code;
    END IF;
END;
$theme_catalog_validation$;

CREATE TABLE news_cluster_theme (
    cluster_id BIGINT NOT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL,
    classification_method TEXT NOT NULL,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_news_cluster_theme_rank CHECK (rank BETWEEN 1 AND 3),
    CONSTRAINT chk_news_cluster_theme_classification_method
        CHECK (classification_method IN ('LLM', 'KEYWORD_FALLBACK')),
    PRIMARY KEY (cluster_id, theme_code),
    CONSTRAINT uq_news_cluster_theme_cluster_rank UNIQUE (cluster_id, rank)
);

CREATE INDEX idx_news_cluster_theme_theme_cluster
    ON news_cluster_theme (theme_code, cluster_id);

CREATE INDEX idx_news_cluster_theme_cluster_rank
    ON news_cluster_theme (cluster_id, rank);

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
    -- NFC/casefold/whitespace-normalized snapshot of page_title and headline.
    search_document TEXT NOT NULL DEFAULT '',
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

CREATE INDEX idx_market_daily_page_search_document
    ON market_daily_page USING GIN (search_document gin_trgm_ops);

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
    -- Snapshot of market_label/title/body/analysis text for trigram search.
    search_document TEXT NOT NULL DEFAULT '',
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

CREATE INDEX idx_market_daily_page_market_search_document
    ON market_daily_page_market USING GIN (search_document gin_trgm_ops);

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
    article_grouping_status TEXT NOT NULL DEFAULT 'UNAVAILABLE',
    article_grouping_generated_at TIMESTAMPTZ NULL,
    article_grouping_issue_code TEXT NULL
        DEFAULT 'SIMILARITY_GROUPING_FAILED',
    article_grouping_algorithm_version TEXT NULL,
    -- Snapshot of normalized cluster and all same-cluster article titles.
    search_document TEXT NOT NULL DEFAULT '',
    CONSTRAINT uq_market_daily_page_market_cluster_order
        UNIQUE (page_market_id, display_order),
    CONSTRAINT chk_market_daily_page_market_cluster_order_positive
        CHECK (display_order > 0),
    CONSTRAINT chk_market_daily_page_market_cluster_article_count_non_negative
        CHECK (article_count >= 0),
    CONSTRAINT chk_page_market_cluster_grouping_status
        CHECK (article_grouping_status IN ('READY', 'UNAVAILABLE')),
    CONSTRAINT chk_page_market_cluster_grouping_ready
        CHECK (
            (
                article_grouping_status = 'READY'
                AND article_grouping_generated_at IS NOT NULL
                AND article_grouping_issue_code IS NULL
            )
            OR (
                article_grouping_status = 'UNAVAILABLE'
                AND article_grouping_generated_at IS NULL
                AND article_grouping_issue_code = 'SIMILARITY_GROUPING_FAILED'
            )
        )
);

CREATE INDEX idx_market_daily_page_market_cluster_market
    ON market_daily_page_market_cluster (page_market_id, display_order);

CREATE INDEX idx_market_daily_page_market_cluster_uid
    ON market_daily_page_market_cluster (cluster_uid);

CREATE INDEX idx_market_daily_page_market_cluster_cluster_id
    ON market_daily_page_market_cluster (cluster_id);

CREATE INDEX idx_market_daily_page_market_cluster_rep_article
    ON market_daily_page_market_cluster (representative_article_id);

CREATE INDEX idx_market_daily_page_market_cluster_search_document
    ON market_daily_page_market_cluster USING GIN (search_document gin_trgm_ops);

CREATE TABLE market_daily_page_market_cluster_theme (
    page_market_cluster_id BIGINT NOT NULL
        REFERENCES market_daily_page_market_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL,
    CONSTRAINT chk_market_daily_page_market_cluster_theme_rank
        CHECK (rank BETWEEN 1 AND 3),
    PRIMARY KEY (page_market_cluster_id, theme_code),
    CONSTRAINT uq_market_daily_page_market_cluster_theme_cluster_rank
        UNIQUE (page_market_cluster_id, rank)
);

CREATE INDEX idx_market_daily_page_market_cluster_theme_theme_cluster
    ON market_daily_page_market_cluster_theme (
        theme_code,
        page_market_cluster_id
    );

CREATE INDEX idx_market_daily_page_market_cluster_theme_cluster_rank
    ON market_daily_page_market_cluster_theme (page_market_cluster_id, rank);

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
    similar_group_rank SMALLINT NULL,
    is_similar_group_representative BOOLEAN NOT NULL DEFAULT TRUE,
    exact_duplicate_count INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_market_daily_page_article_link_order
        UNIQUE (page_market_id, display_order),
    CONSTRAINT chk_market_daily_page_article_link_order_positive
        CHECK (display_order > 0),
    CONSTRAINT chk_market_daily_page_article_link_exact_duplicate_count_non_negative
        CHECK (exact_duplicate_count >= 0)
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
