BEGIN;

ALTER TABLE stock.batch_job
    ADD COLUMN IF NOT EXISTS ai_target_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_attempted_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_success_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_fallback_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_failed_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ai_recovered_count INTEGER NOT NULL DEFAULT 0;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.batch_job'::regclass
          AND conname = 'chk_batch_job_ai_counts_non_negative'
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT chk_batch_job_ai_counts_non_negative
            CHECK (
                ai_target_count >= 0
                AND ai_attempted_count >= 0
                AND ai_success_count >= 0
                AND ai_fallback_count >= 0
                AND ai_failed_count >= 0
                AND ai_recovered_count >= 0
            );
    END IF;
END;
$migration$;

ALTER TABLE stock.ai_summary
    ADD COLUMN IF NOT EXISTS target_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS source_summary_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS attempt_no INTEGER NOT NULL DEFAULT 1;

UPDATE stock.ai_summary
SET target_key = CASE summary_type
    WHEN 'GLOBAL_HEADLINE' THEN 'GLOBAL_HEADLINE'
    WHEN 'MARKET_SUMMARY' THEN
        'MARKET_SUMMARY:' || market_type::TEXT
    WHEN 'CLUSTER_CARD_SUMMARY' THEN
        'CLUSTER_CARD_SUMMARY:' || cluster_id::TEXT
    WHEN 'CLUSTER_DETAIL_ANALYSIS' THEN
        'CLUSTER_DETAIL_ANALYSIS:' || cluster_id::TEXT
END
WHERE target_key IS NULL;

DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM stock.ai_summary
        WHERE target_key IS NULL OR length(btrim(target_key)) = 0
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23502',
            MESSAGE =
                'AI summary rows with incomplete target identity prevent '
                'retry lineage migration.',
            HINT =
                'Populate market_type/cluster_id for the affected summary '
                'type, then rerun the migration.';
    END IF;
END;
$migration$;

ALTER TABLE stock.ai_summary
    ALTER COLUMN target_key SET NOT NULL;

DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.ai_summary'::regclass
          AND conname = 'fk_ai_summary_source_summary'
    ) THEN
        ALTER TABLE stock.ai_summary
            ADD CONSTRAINT fk_ai_summary_source_summary
            FOREIGN KEY (source_summary_id)
            REFERENCES stock.ai_summary(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.ai_summary'::regclass
          AND conname = 'uq_ai_summary_job_target'
    ) THEN
        ALTER TABLE stock.ai_summary
            ADD CONSTRAINT uq_ai_summary_job_target
            UNIQUE (batch_job_id, target_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.ai_summary'::regclass
          AND conname = 'chk_ai_summary_attempt_positive'
    ) THEN
        ALTER TABLE stock.ai_summary
            ADD CONSTRAINT chk_ai_summary_attempt_positive
            CHECK (attempt_no > 0);
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.ai_summary'::regclass
          AND conname = 'chk_ai_summary_target_key_not_blank'
    ) THEN
        ALTER TABLE stock.ai_summary
            ADD CONSTRAINT chk_ai_summary_target_key_not_blank
            CHECK (length(btrim(target_key)) > 0);
    END IF;
END;
$migration$;

CREATE INDEX IF NOT EXISTS idx_ai_summary_source_summary
    ON stock.ai_summary (source_summary_id);

CREATE INDEX IF NOT EXISTS idx_ai_summary_target_effective
    ON stock.ai_summary (target_key, attempt_no DESC, generated_at DESC);

COMMIT;
