BEGIN;

DO $migration$
DECLARE
    duplicate_business_date DATE;
    duplicate_dedupe_hash CHAR(64);
BEGIN
    ALTER TABLE stock.news_article_processed
        DROP CONSTRAINT IF EXISTS uq_news_article_processed_dedupe_hash;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.news_article_processed'::regclass
          AND conname =
              'uq_news_article_processed_business_date_dedupe_hash'
          AND contype = 'u'
    ) THEN
        SELECT business_date, dedupe_hash
        INTO duplicate_business_date, duplicate_dedupe_hash
        FROM stock.news_article_processed
        GROUP BY business_date, dedupe_hash
        HAVING count(*) > 1
        ORDER BY business_date, dedupe_hash
        LIMIT 1;

        IF FOUND THEN
            RAISE EXCEPTION USING
                ERRCODE = '23505',
                MESSAGE = format(
                    'Duplicate processed articles prevent date-scoped dedupe: '
                    'business_date=%s, dedupe_hash=%s.',
                    duplicate_business_date,
                    duplicate_dedupe_hash
                ),
                HINT =
                    'Remove duplicate rows for this date and hash, then rerun '
                    'the migration.';
        END IF;

        DROP INDEX IF EXISTS
            stock.uq_news_article_processed_business_date_dedupe_hash;

        ALTER TABLE stock.news_article_processed
            ADD CONSTRAINT
                uq_news_article_processed_business_date_dedupe_hash
            UNIQUE (business_date, dedupe_hash);
    END IF;
END;
$migration$;

COMMIT;
