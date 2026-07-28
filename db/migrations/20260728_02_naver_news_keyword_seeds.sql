BEGIN;

UPDATE stock.news_search_keyword AS canonical
SET
    is_active = canonical.is_active OR legacy.is_active,
    priority = LEAST(canonical.priority, legacy.priority),
    updated_at = now()
FROM stock.news_search_keyword AS legacy
WHERE canonical.provider_name = 'NAVER_NEWS'
  AND legacy.provider_name = 'NAVER_NEWS_SEARCH'
  AND canonical.market_type = legacy.market_type
  AND lower(btrim(canonical.keyword)) = lower(btrim(legacy.keyword));

DELETE FROM stock.news_search_keyword AS legacy
USING stock.news_search_keyword AS canonical
WHERE legacy.provider_name = 'NAVER_NEWS_SEARCH'
  AND canonical.provider_name = 'NAVER_NEWS'
  AND legacy.market_type = canonical.market_type
  AND lower(btrim(legacy.keyword)) = lower(btrim(canonical.keyword));

UPDATE stock.news_search_keyword
SET
    provider_name = 'NAVER_NEWS',
    updated_at = now()
WHERE provider_name = 'NAVER_NEWS_SEARCH';

INSERT INTO stock.news_search_keyword (
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

UPDATE stock.news_search_keyword
SET
    is_active = TRUE,
    priority = LEAST(priority, 10),
    updated_at = now()
WHERE provider_name = 'NAVER_NEWS'
  AND (
      (market_type = 'US' AND lower(btrim(keyword)) = lower('미국 증시'))
      OR (market_type = 'KR' AND lower(btrim(keyword)) = lower('코스피'))
  )
  AND (NOT is_active OR priority > 10);

COMMIT;
