BEGIN;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_type type_
        JOIN pg_namespace namespace_ ON namespace_.oid = type_.typnamespace
        WHERE namespace_.nspname = 'stock'
          AND type_.typname = 'batch_run_mode_enum'
    ) THEN
        CREATE TYPE stock.batch_run_mode_enum AS ENUM (
            'FULL',
            'PAGE_REBUILD',
            'AI_RETRY'
        );
    END IF;
END;
$$;

ALTER TABLE stock.batch_job
    ADD COLUMN IF NOT EXISTS run_mode stock.batch_run_mode_enum,
    ADD COLUMN IF NOT EXISTS source_job_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS source_page_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL,
    ADD COLUMN IF NOT EXISTS queued_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS available_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER,
    ADD COLUMN IF NOT EXISTS max_attempts INTEGER,
    ADD COLUMN IF NOT EXISTS lease_owner TEXT NULL,
    ADD COLUMN IF NOT EXISTS lease_token UUID NULL,
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS current_step TEXT NULL,
    ADD COLUMN IF NOT EXISTS checkpoint_json JSONB;

UPDATE stock.batch_job
SET
    run_mode = CASE
        WHEN rebuild_page_only THEN 'PAGE_REBUILD'::stock.batch_run_mode_enum
        ELSE 'FULL'::stock.batch_run_mode_enum
    END
WHERE run_mode IS NULL;

UPDATE stock.batch_job
SET
    queued_at = COALESCE(created_at, started_at, now()),
    available_at = COALESCE(created_at, started_at, now()),
    attempt_count = CASE WHEN status = 'RUNNING' THEN 1 ELSE 0 END,
    max_attempts = 3,
    checkpoint_json = '{}'::jsonb
WHERE queued_at IS NULL
   OR available_at IS NULL
   OR attempt_count IS NULL
   OR max_attempts IS NULL
   OR checkpoint_json IS NULL;

ALTER TABLE stock.batch_job
    ALTER COLUMN run_mode SET DEFAULT 'FULL',
    ALTER COLUMN run_mode SET NOT NULL,
    ALTER COLUMN queued_at SET DEFAULT now(),
    ALTER COLUMN queued_at SET NOT NULL,
    ALTER COLUMN available_at SET DEFAULT now(),
    ALTER COLUMN available_at SET NOT NULL,
    ALTER COLUMN attempt_count SET DEFAULT 0,
    ALTER COLUMN attempt_count SET NOT NULL,
    ALTER COLUMN max_attempts SET DEFAULT 3,
    ALTER COLUMN max_attempts SET NOT NULL,
    ALTER COLUMN checkpoint_json SET DEFAULT '{}'::jsonb,
    ALTER COLUMN checkpoint_json SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_batch_job_source_job'
          AND conrelid = 'stock.batch_job'::regclass
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT fk_batch_job_source_job
            FOREIGN KEY (source_job_id)
            REFERENCES stock.batch_job(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'fk_batch_job_source_page'
          AND conrelid = 'stock.batch_job'::regclass
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT fk_batch_job_source_page
            FOREIGN KEY (source_page_id)
            REFERENCES stock.market_daily_page(id)
            ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_batch_job_attempts'
          AND conrelid = 'stock.batch_job'::regclass
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT chk_batch_job_attempts
            CHECK (
                attempt_count >= 0
                AND max_attempts > 0
                AND attempt_count <= max_attempts
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_batch_job_idempotency_key'
          AND conrelid = 'stock.batch_job'::regclass
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT chk_batch_job_idempotency_key
            CHECK (
                idempotency_key IS NULL
                OR length(btrim(idempotency_key)) BETWEEN 1 AND 200
            );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_batch_job_checkpoint_object'
          AND conrelid = 'stock.batch_job'::regclass
    ) THEN
        ALTER TABLE stock.batch_job
            ADD CONSTRAINT chk_batch_job_checkpoint_object
            CHECK (jsonb_typeof(checkpoint_json) = 'object');
    END IF;
END;
$$;

CREATE INDEX IF NOT EXISTS idx_batch_job_source_job_id
    ON stock.batch_job (source_job_id);

CREATE INDEX IF NOT EXISTS idx_batch_job_source_page_id
    ON stock.batch_job (source_page_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_batch_job_idempotency_key
    ON stock.batch_job (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_batch_job_pending_claim
    ON stock.batch_job (available_at, queued_at, id)
    WHERE status = 'PENDING';

CREATE INDEX IF NOT EXISTS idx_batch_job_expired_lease
    ON stock.batch_job (lease_expires_at, id)
    WHERE status = 'RUNNING';

COMMIT;
