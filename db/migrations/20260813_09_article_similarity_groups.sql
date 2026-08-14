BEGIN;

CREATE SCHEMA IF NOT EXISTS stock;
SET search_path TO stock, public;

ALTER TABLE stock.news_cluster
    ADD COLUMN IF NOT EXISTS article_grouping_status TEXT NOT NULL
        DEFAULT 'UNAVAILABLE',
    ADD COLUMN IF NOT EXISTS article_grouping_generated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS article_grouping_issue_code TEXT NULL
        DEFAULT 'SIMILARITY_GROUPING_FAILED';

ALTER TABLE stock.market_daily_page_market_cluster
    ADD COLUMN IF NOT EXISTS article_grouping_status TEXT NOT NULL
        DEFAULT 'UNAVAILABLE',
    ADD COLUMN IF NOT EXISTS article_grouping_generated_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS article_grouping_issue_code TEXT NULL
        DEFAULT 'SIMILARITY_GROUPING_FAILED',
    ADD COLUMN IF NOT EXISTS article_grouping_algorithm_version TEXT NULL;

ALTER TABLE stock.market_daily_page_article_link
    ADD COLUMN IF NOT EXISTS similar_group_rank SMALLINT NULL,
    ADD COLUMN IF NOT EXISTS is_similar_group_representative BOOLEAN NOT NULL
        DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS exact_duplicate_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS stock.news_cluster_similar_group (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_id BIGINT NOT NULL
        REFERENCES stock.news_cluster(id) ON DELETE CASCADE,
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
        REFERENCES stock.news_cluster_article(cluster_id, processed_article_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE IF NOT EXISTS stock.news_cluster_similar_group_article (
    similar_group_id BIGINT NOT NULL
        REFERENCES stock.news_cluster_similar_group(id) ON DELETE CASCADE,
    processed_article_id BIGINT NOT NULL
        REFERENCES stock.news_article_processed(id) ON DELETE RESTRICT,
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

DO $migration_constraints$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster'::regclass
          AND conname = 'chk_news_cluster_article_grouping_status'
    ) THEN
        ALTER TABLE stock.news_cluster
            ADD CONSTRAINT chk_news_cluster_article_grouping_status
            CHECK (article_grouping_status IN ('READY', 'UNAVAILABLE'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster'::regclass
          AND conname = 'chk_news_cluster_article_grouping_ready_generated'
    ) THEN
        ALTER TABLE stock.news_cluster
            ADD CONSTRAINT chk_news_cluster_article_grouping_ready_generated
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
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_market_cluster'::regclass
          AND conname = 'chk_page_market_cluster_grouping_status'
    ) THEN
        ALTER TABLE stock.market_daily_page_market_cluster
            ADD CONSTRAINT chk_page_market_cluster_grouping_status
            CHECK (article_grouping_status IN ('READY', 'UNAVAILABLE'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_market_cluster'::regclass
          AND conname = 'chk_page_market_cluster_grouping_ready'
    ) THEN
        ALTER TABLE stock.market_daily_page_market_cluster
            ADD CONSTRAINT chk_page_market_cluster_grouping_ready
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
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster_similar_group'::regclass
          AND conname = 'chk_similar_group_rank_positive'
    ) THEN
        ALTER TABLE stock.news_cluster_similar_group
            ADD CONSTRAINT chk_similar_group_rank_positive
            CHECK (group_rank > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster_similar_group'::regclass
          AND conname = 'fk_news_cluster_similar_group_representative_membership'
    ) THEN
        ALTER TABLE stock.news_cluster_similar_group
            ADD CONSTRAINT fk_news_cluster_similar_group_representative_membership
            FOREIGN KEY (cluster_id, representative_article_id)
            REFERENCES stock.news_cluster_article(cluster_id, processed_article_id)
            DEFERRABLE INITIALLY DEFERRED;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster_similar_group_article'::regclass
          AND conname = 'chk_similar_group_article_exact_count_non_negative'
    ) THEN
        ALTER TABLE stock.news_cluster_similar_group_article
            ADD CONSTRAINT chk_similar_group_article_exact_count_non_negative
            CHECK (exact_duplicate_count >= 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_cluster_similar_group_article'::regclass
          AND conname = 'chk_similar_group_article_rank_positive'
    ) THEN
        ALTER TABLE stock.news_cluster_similar_group_article
            ADD CONSTRAINT chk_similar_group_article_rank_positive
            CHECK (article_rank > 0);
    END IF;
END;
$migration_constraints$;

CREATE INDEX IF NOT EXISTS idx_news_cluster_similar_group_cluster_rank
    ON stock.news_cluster_similar_group (cluster_id, group_rank);

CREATE INDEX IF NOT EXISTS idx_news_cluster_similar_group_article_processed
    ON stock.news_cluster_similar_group_article (processed_article_id);

COMMIT;
