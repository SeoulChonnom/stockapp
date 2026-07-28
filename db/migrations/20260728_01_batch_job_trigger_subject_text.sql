BEGIN;

DO $migration$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'stock'
          AND table_name = 'batch_job'
          AND column_name = 'triggered_by_user_id'
          AND data_type <> 'text'
    ) THEN
        ALTER TABLE stock.batch_job
            ALTER COLUMN triggered_by_user_id TYPE TEXT
            USING triggered_by_user_id::text;
    END IF;
END;
$migration$;

COMMIT;
