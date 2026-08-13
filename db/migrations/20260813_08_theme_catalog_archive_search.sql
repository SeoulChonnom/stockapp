BEGIN;

CREATE SCHEMA IF NOT EXISTS stock;
SET search_path TO stock, public;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

ALTER TABLE stock.market_daily_page_market
    ADD COLUMN IF NOT EXISTS search_document TEXT NOT NULL DEFAULT '';

ALTER TABLE stock.market_daily_page_market_cluster
    ADD COLUMN IF NOT EXISTS search_document TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS stock.theme_catalog (
    code TEXT PRIMARY KEY,
    parent_code TEXT NULL REFERENCES stock.theme_catalog(code) ON DELETE RESTRICT,
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

CREATE TABLE IF NOT EXISTS stock.news_cluster_theme (
    cluster_id BIGINT NOT NULL
        REFERENCES stock.news_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL
        REFERENCES stock.theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL,
    classification_method TEXT NOT NULL,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_news_cluster_theme_rank CHECK (rank BETWEEN 1 AND 3),
    CONSTRAINT chk_news_cluster_theme_classification_method
        CHECK (classification_method IN ('LLM', 'KEYWORD_FALLBACK')),
    PRIMARY KEY (cluster_id, theme_code),
    CONSTRAINT uq_news_cluster_theme_cluster_rank UNIQUE (cluster_id, rank)
);

CREATE TABLE IF NOT EXISTS stock.market_daily_page_market_cluster_theme (
    page_market_cluster_id BIGINT NOT NULL
        REFERENCES stock.market_daily_page_market_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL
        REFERENCES stock.theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL,
    CONSTRAINT chk_market_daily_page_market_cluster_theme_rank
        CHECK (rank BETWEEN 1 AND 3),
    PRIMARY KEY (page_market_cluster_id, theme_code),
    CONSTRAINT uq_market_daily_page_market_cluster_theme_cluster_rank
        UNIQUE (page_market_cluster_id, rank)
);

DO $migration_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.theme_catalog'::regclass
          AND conname = 'uq_theme_catalog_parent_sort_order'
    ) THEN
        ALTER TABLE stock.theme_catalog
            ADD CONSTRAINT uq_theme_catalog_parent_sort_order
            UNIQUE (parent_code, sort_order);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.theme_catalog'::regclass
          AND conname = 'chk_theme_catalog_code_format'
    ) THEN
        ALTER TABLE stock.theme_catalog
            ADD CONSTRAINT chk_theme_catalog_code_format
            CHECK (code ~ '^[A-Z0-9_]+$');
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.theme_catalog'::regclass
          AND conname = 'chk_theme_catalog_not_self_parent'
    ) THEN
        ALTER TABLE stock.theme_catalog
            ADD CONSTRAINT chk_theme_catalog_not_self_parent
            CHECK (parent_code IS NULL OR code <> parent_code);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.theme_catalog'::regclass
          AND contype = 'f'
          AND confrelid = 'stock.theme_catalog'::regclass
    ) THEN
        ALTER TABLE stock.theme_catalog
            ADD CONSTRAINT fk_theme_catalog_parent
            FOREIGN KEY (parent_code)
            REFERENCES stock.theme_catalog(code)
            ON DELETE RESTRICT;
    END IF;
END;
$migration_constraints$;

CREATE INDEX IF NOT EXISTS idx_theme_catalog_parent_sort_order
    ON stock.theme_catalog (parent_code, sort_order);

CREATE INDEX IF NOT EXISTS idx_news_cluster_theme_theme_cluster
    ON stock.news_cluster_theme (theme_code, cluster_id);

CREATE INDEX IF NOT EXISTS idx_news_cluster_theme_cluster_rank
    ON stock.news_cluster_theme (cluster_id, rank);

CREATE INDEX IF NOT EXISTS
    idx_market_daily_page_market_cluster_theme_theme_cluster
    ON stock.market_daily_page_market_cluster_theme (
        theme_code,
        page_market_cluster_id
    );

CREATE INDEX IF NOT EXISTS
    idx_market_daily_page_market_cluster_theme_cluster_rank
    ON stock.market_daily_page_market_cluster_theme (
        page_market_cluster_id,
        rank
    );

CREATE INDEX IF NOT EXISTS idx_market_daily_page_market_search_document
    ON stock.market_daily_page_market USING GIN (search_document gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_market_daily_page_market_cluster_search_document
    ON stock.market_daily_page_market_cluster
    USING GIN (search_document gin_trgm_ops);

INSERT INTO stock.theme_catalog (
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
        FROM stock.theme_catalog
        UNION ALL
        SELECT
            parent.code,
            parent.parent_code,
            lineage.path || parent.code,
            parent.code = ANY(lineage.path)
        FROM lineage
        JOIN stock.theme_catalog AS parent
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

COMMIT;
