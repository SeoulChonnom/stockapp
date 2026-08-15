BEGIN;

CREATE SCHEMA IF NOT EXISTS stock;
SET search_path TO stock, public;

-- 20260813_09 added similar_group_rank as a nullable column without a default
-- while its two sibling columns received one, so every article link written
-- before that migration kept NULL. The page assembler requires a positive rank
-- and rejects the whole payload otherwise, which turns every pre-existing page
-- into a 500. Treat each legacy link as its own single-article group.
UPDATE stock.market_daily_page_article_link
   SET similar_group_rank = 1
 WHERE similar_group_rank IS NULL;

ALTER TABLE stock.market_daily_page_article_link
    ALTER COLUMN similar_group_rank SET DEFAULT 1,
    ALTER COLUMN similar_group_rank SET NOT NULL;

DO $migration_constraints$
BEGIN
    -- Guards match on the constraint definition, not its name: the canonical
    -- name below is longer than PostgreSQL's 63-byte identifier limit and is
    -- silently truncated, so a name comparison would never find it and would
    -- try to add the same constraint again on a second run. strpos is used
    -- instead of LIKE because this body also runs through psycopg, which reads
    -- a literal percent sign as a parameter placeholder.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_article_link'::regclass
          AND contype = 'c'
          AND strpos(pg_get_constraintdef(oid), 'similar_group_rank > 0') > 0
    ) THEN
        ALTER TABLE stock.market_daily_page_article_link
            ADD CONSTRAINT chk_market_daily_page_article_link_group_rank_positive
            CHECK (similar_group_rank > 0);
    END IF;

    -- db/schema_postgresql.sql declares this check, but 20260813_09 only added
    -- the column and never the constraint, so upgraded databases lack it while
    -- fresh ones have it. Converge both paths here.
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'stock.market_daily_page_article_link'::regclass
          AND contype = 'c'
          AND strpos(pg_get_constraintdef(oid), 'exact_duplicate_count >= 0') > 0
    ) THEN
        ALTER TABLE stock.market_daily_page_article_link
            ADD CONSTRAINT
                chk_market_daily_page_article_link_exact_duplicate_count_non_negative
            CHECK (exact_duplicate_count >= 0);
    END IF;
END;
$migration_constraints$;

COMMIT;
