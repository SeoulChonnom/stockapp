BEGIN;

SET search_path TO stock, public;

-- Seed data derived from the frontend mock objects provided in the request.
-- Note:
-- 1. The schema has no dedicated "market" column on batch_job, so the original
--    mock market labels are preserved in log_summary and batch_job_event.context_json.
-- 2. Several archive pages are header-only because the mock data only included
--    full market/cluster detail for 2026-03-17 and 2026-03-12.

INSERT INTO batch_job (
    id,
    job_name,
    business_date,
    status,
    trigger_type,
    force_run,
    rebuild_page_only,
    market_scope,
    started_at,
    ended_at,
    duration_seconds,
    raw_news_count,
    processed_news_count,
    cluster_count,
    partial_message,
    error_code,
    error_message,
    log_summary,
    created_at,
    updated_at
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        1001,
        'market_daily_batch',
        DATE '2026-03-17',
        'SUCCESS',
        'SCHEDULED',
        FALSE,
        FALSE,
        'GLOBAL',
        TIMESTAMPTZ '2026-03-17 08:00:01+09',
        TIMESTAMPTZ '2026-03-17 08:14:12+09',
        851,
        77,
        32,
        9,
        NULL,
        NULL,
        NULL,
        '[US Market] 정상 처리. 시장 데이터, 기사 수집, 클러스터링이 모두 SLA 안에서 종료됐습니다.',
        TIMESTAMPTZ '2026-03-17 08:00:01+09',
        TIMESTAMPTZ '2026-03-17 08:14:12+09'
    ),
    (
        1002,
        'market_daily_batch',
        DATE '2026-03-17',
        'PARTIAL',
        'SCHEDULED',
        FALSE,
        FALSE,
        'GLOBAL',
        TIMESTAMPTZ '2026-03-17 16:30:05+09',
        TIMESTAMPTZ '2026-03-17 16:51:20+09',
        1275,
        112,
        88,
        14,
        'KR 미러 소스 일부 지연으로 기사 번역 결과가 일부 누락됐습니다.',
        NULL,
        NULL,
        '[KR Market] KR 미러 소스 일부 지연으로 기사 번역 결과가 일부 누락됐습니다.',
        TIMESTAMPTZ '2026-03-17 16:30:05+09',
        TIMESTAMPTZ '2026-03-17 16:51:20+09'
    ),
    (
        1003,
        'market_daily_batch',
        DATE '2026-03-16',
        'FAILED',
        'SCHEDULED',
        FALSE,
        FALSE,
        'GLOBAL',
        TIMESTAMPTZ '2026-03-16 08:00:02+09',
        TIMESTAMPTZ '2026-03-16 08:02:14+09',
        132,
        12,
        0,
        0,
        NULL,
        'WS_TIMEOUT',
        'CRITICAL: WebSocket timeout during NYSE scrape. Peer disconnected after 3000ms.',
        '[US Market] CRITICAL: WebSocket timeout during NYSE scrape. Peer disconnected after 3000ms.',
        TIMESTAMPTZ '2026-03-16 08:00:02+09',
        TIMESTAMPTZ '2026-03-16 08:02:14+09'
    ),
    (
        1004,
        'market_daily_batch',
        DATE '2026-03-16',
        'SUCCESS',
        'SCHEDULED',
        FALSE,
        FALSE,
        'GLOBAL',
        TIMESTAMPTZ '2026-03-16 16:30:01+09',
        TIMESTAMPTZ '2026-03-16 16:44:55+09',
        894,
        108,
        108,
        15,
        NULL,
        NULL,
        NULL,
        '[KR Market] 정상 처리. 한국 기사군 동기화와 클러스터 생성이 모두 완료됐습니다.',
        TIMESTAMPTZ '2026-03-16 16:30:01+09',
        TIMESTAMPTZ '2026-03-16 16:44:55+09'
    );

INSERT INTO batch_job_event (
    batch_job_id,
    step_code,
    level,
    message,
    context_json,
    created_at
)
VALUES
    (
        1001,
        'FINALIZE_JOB',
        'INFO',
        'Mock seed batch run inserted.',
        '{"mockJobId":"job-99281-b","market":"US Market","counts":"77 / 32 / 9"}'::jsonb,
        TIMESTAMPTZ '2026-03-17 08:14:12+09'
    ),
    (
        1002,
        'FINALIZE_JOB',
        'WARN',
        'Mock seed batch run inserted with partial status.',
        '{"mockJobId":"job-99282-a","market":"KR Market","counts":"112 / 88 / 14"}'::jsonb,
        TIMESTAMPTZ '2026-03-17 16:51:20+09'
    ),
    (
        1003,
        'FINALIZE_JOB',
        'ERROR',
        'Mock seed batch run inserted with failed status.',
        '{"mockJobId":"job-99281-b-failed","market":"US Market","counts":"12 / 0 / 0"}'::jsonb,
        TIMESTAMPTZ '2026-03-16 08:02:14+09'
    ),
    (
        1004,
        'FINALIZE_JOB',
        'INFO',
        'Mock seed batch run inserted.',
        '{"mockJobId":"job-99270-z","market":"KR Market","counts":"108 / 108 / 15"}'::jsonb,
        TIMESTAMPTZ '2026-03-16 16:44:55+09'
    );

INSERT INTO market_index_daily (
    id,
    business_date,
    market_type,
    index_code,
    index_name,
    close_price,
    change_value,
    change_percent,
    high_price,
    low_price,
    currency_code,
    provider_name,
    collected_at,
    created_at
)
OVERRIDING SYSTEM VALUE
VALUES
    (5001, DATE '2026-03-17', 'US', 'NASDAQ', 'NASDAQ Composite', 16274.94, 120.33, 0.74, 16302.11, 16180.45, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5002, DATE '2026-03-17', 'US', 'SP500', 'S&P 500', 5117.09, 32.55, 0.64, 5130.42, 5098.22, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5003, DATE '2026-03-17', 'US', 'DJI', 'DOW JONES', 38790.43, -15.62, -0.04, 38920.15, 38710.88, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5004, DATE '2026-03-17', 'KR', 'KOSPI', 'KOSPI', 2781.42, 23.80, 0.86, 2786.11, 2759.84, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5005, DATE '2026-03-17', 'KR', 'KOSDAQ', 'KOSDAQ', 912.66, 4.25, 0.47, 915.04, 905.12, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5006, DATE '2026-03-17', 'KR', 'USDKRW', 'USD/KRW', 1318.20, -6.10, -0.46, 1324.00, 1316.50, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-18 06:12:00+09', TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (5007, DATE '2026-03-12', 'US', 'NASDAQ', 'NASDAQ Composite', 16274.94, 120.33, 0.74, 16302.11, 16180.45, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09'),
    (5008, DATE '2026-03-12', 'US', 'SP500', 'S&P 500', 5117.09, 32.55, 0.64, 5130.42, 5098.22, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09'),
    (5009, DATE '2026-03-12', 'US', 'DJI', 'DOW JONES', 38790.43, -15.62, -0.04, 38920.15, 38710.88, 'USD', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09'),
    (5010, DATE '2026-03-12', 'KR', 'KOSPI', 'KOSPI', 2781.42, 23.80, 0.86, 2786.11, 2759.84, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09'),
    (5011, DATE '2026-03-12', 'KR', 'KOSDAQ', 'KOSDAQ', 912.66, 4.25, 0.47, 915.04, 905.12, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09'),
    (5012, DATE '2026-03-12', 'KR', 'USDKRW', 'USD/KRW', 1318.20, -6.10, -0.46, 1324.00, 1316.50, 'KRW', 'mock_seed', TIMESTAMPTZ '2026-03-13 06:09:00+09', TIMESTAMPTZ '2026-03-13 06:09:00+09');

INSERT INTO news_article_processed (
    id,
    business_date,
    market_type,
    dedupe_hash,
    canonical_title,
    publisher_name,
    published_at,
    origin_link,
    naver_link,
    source_summary,
    article_body_excerpt,
    content_json,
    created_at,
    updated_at
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        3001,
        DATE '2026-03-17',
        'US',
        '1111111111111111111111111111111111111111111111111111111111111111',
        '나스닥, 기술주 중심의 강력한 반등... 금리 인하론에 힘 실려',
        '월스트리트 저널',
        TIMESTAMPTZ '2026-03-17 08:15:00-04',
        'https://example.com/original-wsj',
        'https://example.com/naver-wsj',
        '경제·금융 전문 매체',
        '금리 인하 기대와 대형 기술주의 반등을 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3002,
        DATE '2026-03-17',
        'US',
        '2222222222222222222222222222222222222222222222222222222222222222',
        '반도체 지수 3% 급등, 엔비디아 사상 최고가 경신 임박',
        '블룸버그',
        TIMESTAMPTZ '2026-03-17 09:30:00-04',
        'https://example.com/original-bloomberg',
        'https://example.com/naver-bloomberg',
        '글로벌 금융 통신사',
        '엔비디아와 반도체 지수 상승을 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3003,
        DATE '2026-03-17',
        'US',
        '3333333333333333333333333333333333333333333333333333333333333333',
        '인플레이션 둔화 신호에 국채 금리 급락, 대형 기술주 일제히 상승',
        '로이터',
        TIMESTAMPTZ '2026-03-17 11:05:00-04',
        'https://example.com/original-reuters',
        'https://example.com/naver-reuters',
        '국제 뉴스 통신사',
        '국채 금리 하락과 기술주 상승을 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3004,
        DATE '2026-03-17',
        'US',
        '4444444444444444444444444444444444444444444444444444444444444444',
        '연준 위원들의 비둘기파적 발언, 시장은 6월 인하 가능성 80%로 반영',
        '파이낸셜 타임즈',
        TIMESTAMPTZ '2026-03-17 13:45:00-04',
        'https://example.com/original-ft',
        'https://example.com/naver-ft',
        '글로벌 경제 전문지',
        '연준 발언과 금리 기대를 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3005,
        DATE '2026-03-17',
        'US',
        '5555555555555555555555555555555555555555555555555555555555555555',
        '예상 밑돈 PPI에 성장주 랠리 재점화',
        'CNBC',
        TIMESTAMPTZ '2026-03-17 10:10:00-04',
        'https://example.com/original-cnbc',
        'https://example.com/naver-cnbc',
        '미국 비즈니스 방송',
        'PPI 둔화와 금리 인하 기대를 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3006,
        DATE '2026-03-17',
        'KR',
        '6666666666666666666666666666666666666666666666666666666666666666',
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        '한국경제',
        TIMESTAMPTZ '2026-03-17 14:10:00+09',
        'https://example.com/original-hk',
        'https://example.com/naver-hk',
        '국내 경제지',
        'HBM 기대와 외국인 순매수를 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    ),
    (
        3007,
        DATE '2026-03-17',
        'KR',
        '7777777777777777777777777777777777777777777777777777777777777777',
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '매일경제',
        TIMESTAMPTZ '2026-03-17 15:20:00+09',
        'https://example.com/original-mk',
        'https://example.com/naver-mk',
        '국내 종합 경제지',
        '업종 순환과 대형주 집중 흐름을 다룬 기사 요약입니다.',
        '{}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:10:00+09',
        TIMESTAMPTZ '2026-03-18 06:10:00+09'
    );

INSERT INTO news_cluster (
    id,
    cluster_uid,
    business_date,
    market_type,
    cluster_rank,
    title,
    summary_short,
    summary_long,
    analysis_paragraphs_json,
    tags_json,
    representative_article_id,
    article_count,
    created_at,
    updated_at
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        4001,
        'a8d5d5f8-fec5-4caa-b5ef-91a1c0b5d678',
        DATE '2026-03-17',
        'US',
        1,
        '금리 인하 기대와 반도체 실적 개선 기대가 기술주를 견인',
        '차세대 AI 칩 공개 기대와 투자 사이클 확장 전망이 맞물리며 반도체 업종 전반의 밸류에이션이 재평가되는 흐름이 나타났습니다.',
        '연방준비제도의 금리 인하 기대와 반도체 업황 개선 기대가 기술주 랠리를 견인했습니다.',
        '[
          "연방준비제도의 금리 인하 경로가 더 명확해졌다는 해석이 확산되며 고밸류 성장주에 대한 할인율 부담이 완화됐습니다.",
          "엔비디아와 AMD를 포함한 반도체 업종은 AI 서버 수요와 차세대 칩 공개 기대가 동시에 반영되며 지수 대비 초과수익을 기록했습니다.",
          "생산자물가 둔화가 장기 금리 안정으로 이어지면서, 단기 모멘텀뿐 아니라 실적 시즌을 앞둔 포지셔닝 성격의 매수도 유입됐습니다.",
          "다만 유가와 지정학 이슈가 여전히 남아 있어, 향후 랠리 지속성은 거시 변수의 추가 안정 여부에 달려 있습니다."
        ]'::jsonb,
        '["금리", "기술주", "반도체", "나스닥"]'::jsonb,
        3001,
        24,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        TIMESTAMPTZ '2026-03-18 14:30:00+09'
    ),
    (
        4002,
        'aa13f5f0-2152-41f2-b16d-f0001a7298a4',
        DATE '2026-03-17',
        'US',
        2,
        '생산자물가 둔화가 금리 인하 기대를 키우며 성장주 강세',
        '예상치를 밑돈 물가 지표가 국채 금리를 끌어내렸고, 고밸류 기술주의 멀티플 확장 기대가 재점화됐습니다.',
        'PPI 둔화와 금리 하락이 성장주 강세를 이끌었습니다.',
        '[]'::jsonb,
        '["PPI", "FED", "RATES"]'::jsonb,
        3005,
        4,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        TIMESTAMPTZ '2026-03-18 06:12:00+09'
    ),
    (
        4003,
        '338e20f0-8d76-4a20-9e8f-7ad6105f61bf',
        DATE '2026-03-17',
        'KR',
        1,
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        'HBM 수요 기대와 미국 반도체 강세가 국내 대형 메모리주로 전이되면서 지수 상단을 끌어올렸습니다.',
        '외국인 순매수와 반도체 대형주 강세가 코스피 반등을 견인했습니다.',
        '[]'::jsonb,
        '["KOSPI", "HBM", "FOREIGNERS"]'::jsonb,
        3006,
        5,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        TIMESTAMPTZ '2026-03-18 06:12:00+09'
    ),
    (
        4004,
        'a2c7c439-c68e-4ef5-8c23-f267d87a4722',
        DATE '2026-03-17',
        'KR',
        2,
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '업종 순환이 뚜렷해지면서 지수 상승과 체감 난이도 간 괴리가 커졌고, 시장은 대형주 중심 방어적 랠리 양상을 보였습니다.',
        '업종 순환이 심화되며 대형주 중심 장세가 강화됐습니다.',
        '[]'::jsonb,
        '["KOSDAQ", "ROTATION", "LARGE_CAP"]'::jsonb,
        3007,
        3,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        TIMESTAMPTZ '2026-03-18 06:12:00+09'
    );

INSERT INTO news_cluster_article (
    cluster_id,
    processed_article_id,
    article_rank,
    created_at
)
VALUES
    (4001, 3001, 1, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4001, 3002, 2, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4001, 3003, 3, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4001, 3004, 4, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4002, 3005, 1, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4003, 3006, 1, TIMESTAMPTZ '2026-03-18 06:12:00+09'),
    (4004, 3007, 1, TIMESTAMPTZ '2026-03-18 06:12:00+09');

INSERT INTO market_daily_page (
    id,
    business_date,
    version_no,
    page_title,
    status,
    global_headline,
    partial_message,
    generated_at,
    raw_news_count,
    processed_news_count,
    cluster_count,
    last_updated_at,
    batch_job_id,
    metadata_json,
    created_at
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        2001,
        DATE '2026-03-17',
        1,
        '2026-03-17 시장 요약',
        'READY',
        '기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세',
        NULL,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        189,
        120,
        23,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        1001,
        '{"seed":"latestMarketSnapshot"}'::jsonb,
        TIMESTAMPTZ '2026-03-18 06:12:00+09'
    ),
    (
        2002,
        DATE '2026-03-16',
        1,
        '2026-03-16 시장 요약',
        'READY',
        '금리 동결 기대와 소비 둔화 신호가 혼재하며 증시 혼조 마감',
        NULL,
        TIMESTAMPTZ '2026-03-16 06:12:45+09',
        120,
        108,
        15,
        TIMESTAMPTZ '2026-03-16 06:12:45+09',
        1004,
        '{"seed":"archiveRecord"}'::jsonb,
        TIMESTAMPTZ '2026-03-16 06:12:45+09'
    ),
    (
        2003,
        DATE '2026-03-15',
        1,
        '2026-03-15 시장 요약',
        'PARTIAL',
        '데이터 수집 지연으로 일부 섹터 뉴스 클러스터가 누락된 상태',
        '데이터 수집 지연으로 일부 섹터 뉴스 클러스터가 누락된 상태',
        TIMESTAMPTZ '2026-03-15 07:45:10+09',
        0,
        0,
        0,
        TIMESTAMPTZ '2026-03-15 07:45:10+09',
        1004,
        '{"seed":"archiveRecord"}'::jsonb,
        TIMESTAMPTZ '2026-03-15 07:45:10+09'
    ),
    (
        2004,
        DATE '2026-03-14',
        1,
        '2026-03-14 시장 요약',
        'FAILED',
        '에너지 가격 급등이 인플레이션 우려를 키우며 성장주 변동성 확대',
        '에너지 가격 급등이 인플레이션 우려를 키우며 성장주 변동성 확대',
        TIMESTAMPTZ '2026-03-14 05:58:09+09',
        0,
        0,
        0,
        TIMESTAMPTZ '2026-03-14 05:58:09+09',
        1003,
        '{"seed":"archiveRecord"}'::jsonb,
        TIMESTAMPTZ '2026-03-14 05:58:09+09'
    ),
    (
        2005,
        DATE '2026-03-13',
        1,
        '2026-03-13 시장 요약',
        'READY',
        '외국인 순매수 확대와 메모리 업황 개선이 코스피를 지지',
        NULL,
        TIMESTAMPTZ '2026-03-13 06:08:59+09',
        0,
        0,
        0,
        TIMESTAMPTZ '2026-03-13 06:08:59+09',
        1004,
        '{"seed":"archiveRecord"}'::jsonb,
        TIMESTAMPTZ '2026-03-13 06:08:59+09'
    ),
    (
        2006,
        DATE '2026-03-12',
        1,
        '2026-03-12 시장 요약',
        'READY',
        '고용 둔화 신호와 실적 기대가 맞물리며 위험자산 선호 회복',
        NULL,
        TIMESTAMPTZ '2026-03-13 06:09:00+09',
        189,
        120,
        23,
        TIMESTAMPTZ '2026-03-13 06:09:00+09',
        1001,
        '{"seed":"archiveMarketSnapshots"}'::jsonb,
        TIMESTAMPTZ '2026-03-13 06:09:00+09'
    );

INSERT INTO market_daily_page_market (
    id,
    page_id,
    market_type,
    display_order,
    market_label,
    summary_title,
    summary_body,
    analysis_background_json,
    analysis_key_themes_json,
    analysis_outlook,
    raw_news_count,
    processed_news_count,
    cluster_count,
    last_updated_at,
    partial_message,
    metadata_json
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        2101,
        2001,
        'US',
        1,
        '미국 증시 일간 요약',
        '반도체와 대형 성장주가 시장 주도권을 회복',
        'PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했습니다. AI 인프라 투자 기대가 엔비디아와 AMD 같은 반도체 종목 전반에 매수세를 확산시켰습니다.',
        '["PPI 둔화와 장기 금리 하락이 성장주 밸류에이션 부담을 완화했습니다."]'::jsonb,
        '["AI 인프라", "반도체", "대형 성장주"]'::jsonb,
        '거시 변수 안정 여부가 추가 랠리의 핵심 변수입니다.',
        77,
        32,
        9,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        NULL,
        '{}'::jsonb
    ),
    (
        2102,
        2001,
        'KR',
        2,
        '한국 증시 일간 요약',
        '외국인 순매수와 반도체 대형주 강세가 코스피 반등 견인',
        '원화 안정과 메모리 가격 기대가 겹치면서 외국인 매수세가 확대됐습니다. 대형주 중심 반등이 나타났지만 내수주는 여전히 선별 장세가 이어졌습니다.',
        '["원화 안정과 메모리 가격 기대가 외국인 매수 유입을 뒷받침했습니다."]'::jsonb,
        '["외국인 순매수", "메모리", "대형주"]'::jsonb,
        '순환매 강도에 따라 체감 장세는 계속 차별화될 수 있습니다.',
        112,
        88,
        14,
        TIMESTAMPTZ '2026-03-18 06:12:00+09',
        NULL,
        '{}'::jsonb
    ),
    (
        2103,
        2006,
        'US',
        1,
        '미국 증시 일간 요약',
        '반도체와 대형 성장주가 시장 주도권을 회복',
        'PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했습니다. AI 인프라 투자 기대가 엔비디아와 AMD 같은 반도체 종목 전반에 매수세를 확산시켰습니다.',
        '["고용 둔화 신호가 위험자산 선호 회복으로 이어졌습니다."]'::jsonb,
        '["실적 기대", "성장주", "AI"]'::jsonb,
        '매크로 둔화가 완만하게 이어질 경우 성장주 선호가 유지될 수 있습니다.',
        77,
        32,
        9,
        TIMESTAMPTZ '2026-03-13 06:09:00+09',
        NULL,
        '{}'::jsonb
    ),
    (
        2104,
        2006,
        'KR',
        2,
        '한국 증시 일간 요약',
        '외국인 순매수와 반도체 대형주 강세가 코스피 반등 견인',
        '원화 안정과 메모리 가격 기대가 겹치면서 외국인 매수세가 확대됐습니다. 대형주 중심 반등이 나타났지만 내수주는 여전히 선별 장세가 이어졌습니다.',
        '["외국인 매수 확대와 메모리 업황 기대가 지수 하단을 지지했습니다."]'::jsonb,
        '["외국인 순매수", "반도체", "코스피"]'::jsonb,
        '대형주 주도 장세가 이어지는 가운데 업종별 차별화가 예상됩니다.',
        112,
        88,
        14,
        TIMESTAMPTZ '2026-03-13 06:09:00+09',
        NULL,
        '{}'::jsonb
    );

INSERT INTO market_daily_page_market_index (
    id,
    page_market_id,
    market_index_daily_id,
    display_order,
    index_code,
    index_name,
    close_price,
    change_value,
    change_percent,
    high_price,
    low_price,
    currency_code
)
OVERRIDING SYSTEM VALUE
VALUES
    (2201, 2101, 5001, 1, 'NASDAQ', 'NASDAQ Composite', 16274.94, 120.33, 0.74, 16302.11, 16180.45, 'USD'),
    (2202, 2101, 5002, 2, 'SP500', 'S&P 500', 5117.09, 32.55, 0.64, 5130.42, 5098.22, 'USD'),
    (2203, 2101, 5003, 3, 'DJI', 'DOW JONES', 38790.43, -15.62, -0.04, 38920.15, 38710.88, 'USD'),
    (2204, 2102, 5004, 1, 'KOSPI', 'KOSPI', 2781.42, 23.80, 0.86, 2786.11, 2759.84, 'KRW'),
    (2205, 2102, 5005, 2, 'KOSDAQ', 'KOSDAQ', 912.66, 4.25, 0.47, 915.04, 905.12, 'KRW'),
    (2206, 2102, 5006, 3, 'USDKRW', 'USD/KRW', 1318.20, -6.10, -0.46, 1324.00, 1316.50, 'KRW'),
    (2207, 2103, 5007, 1, 'NASDAQ', 'NASDAQ Composite', 16274.94, 120.33, 0.74, 16302.11, 16180.45, 'USD'),
    (2208, 2103, 5008, 2, 'SP500', 'S&P 500', 5117.09, 32.55, 0.64, 5130.42, 5098.22, 'USD'),
    (2209, 2103, 5009, 3, 'DJI', 'DOW JONES', 38790.43, -15.62, -0.04, 38920.15, 38710.88, 'USD'),
    (2210, 2104, 5010, 1, 'KOSPI', 'KOSPI', 2781.42, 23.80, 0.86, 2786.11, 2759.84, 'KRW'),
    (2211, 2104, 5011, 2, 'KOSDAQ', 'KOSDAQ', 912.66, 4.25, 0.47, 915.04, 905.12, 'KRW'),
    (2212, 2104, 5012, 3, 'USDKRW', 'USD/KRW', 1318.20, -6.10, -0.46, 1324.00, 1316.50, 'KRW');

INSERT INTO market_daily_page_market_cluster (
    id,
    page_market_id,
    cluster_id,
    cluster_uid,
    display_order,
    title,
    summary,
    article_count,
    tags_json,
    representative_article_id,
    representative_title,
    representative_publisher_name,
    representative_published_at,
    representative_origin_link,
    representative_naver_link
)
OVERRIDING SYSTEM VALUE
VALUES
    (
        2301,
        2101,
        4001,
        'a8d5d5f8-fec5-4caa-b5ef-91a1c0b5d678',
        1,
        '엔비디아 GTC 2026 기대감에 반도체주 동반 랠리',
        '차세대 AI 칩 공개 기대와 투자 사이클 확장 전망이 맞물리며 반도체 업종 전반의 밸류에이션이 재평가되는 흐름이 나타났습니다.',
        6,
        '["NVIDIA", "SEMICONDUCTOR", "AI"]'::jsonb,
        3001,
        '나스닥, 기술주 중심의 강력한 반등... 금리 인하론에 힘 실려',
        '월스트리트 저널',
        TIMESTAMPTZ '2026-03-17 08:15:00-04',
        'https://example.com/original-wsj',
        'https://example.com/naver-wsj'
    ),
    (
        2302,
        2101,
        4002,
        'aa13f5f0-2152-41f2-b16d-f0001a7298a4',
        2,
        '생산자물가 둔화가 금리 인하 기대를 키우며 성장주 강세',
        '예상치를 밑돈 물가 지표가 국채 금리를 끌어내렸고, 고밸류 기술주의 멀티플 확장 기대가 재점화됐습니다.',
        4,
        '["PPI", "FED", "RATES"]'::jsonb,
        3005,
        '예상 밑돈 PPI에 성장주 랠리 재점화',
        'CNBC',
        TIMESTAMPTZ '2026-03-17 10:10:00-04',
        'https://example.com/original-cnbc',
        'https://example.com/naver-cnbc'
    ),
    (
        2303,
        2102,
        4003,
        '338e20f0-8d76-4a20-9e8f-7ad6105f61bf',
        1,
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        'HBM 수요 기대와 미국 반도체 강세가 국내 대형 메모리주로 전이되면서 지수 상단을 끌어올렸습니다.',
        5,
        '["KOSPI", "HBM", "FOREIGNERS"]'::jsonb,
        3006,
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        '한국경제',
        TIMESTAMPTZ '2026-03-17 14:10:00+09',
        'https://example.com/original-hk',
        'https://example.com/naver-hk'
    ),
    (
        2304,
        2102,
        4004,
        'a2c7c439-c68e-4ef5-8c23-f267d87a4722',
        2,
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '업종 순환이 뚜렷해지면서 지수 상승과 체감 난이도 간 괴리가 커졌고, 시장은 대형주 중심 방어적 랠리 양상을 보였습니다.',
        3,
        '["KOSDAQ", "ROTATION", "LARGE_CAP"]'::jsonb,
        3007,
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '매일경제',
        TIMESTAMPTZ '2026-03-17 15:20:00+09',
        'https://example.com/original-mk',
        'https://example.com/naver-mk'
    ),
    (
        2305,
        2103,
        NULL,
        'a8d5d5f8-fec5-4caa-b5ef-91a1c0b5d678',
        1,
        '엔비디아 GTC 2026 기대감에 반도체주 동반 랠리',
        '차세대 AI 칩 공개 기대와 투자 사이클 확장 전망이 맞물리며 반도체 업종 전반의 밸류에이션이 재평가되는 흐름이 나타났습니다.',
        6,
        '["NVIDIA", "SEMICONDUCTOR", "AI"]'::jsonb,
        3001,
        '나스닥, 기술주 중심의 강력한 반등... 금리 인하론에 힘 실려',
        '월스트리트 저널',
        TIMESTAMPTZ '2026-03-17 08:15:00-04',
        'https://example.com/original-wsj',
        'https://example.com/naver-wsj'
    ),
    (
        2306,
        2103,
        NULL,
        'aa13f5f0-2152-41f2-b16d-f0001a7298a4',
        2,
        '생산자물가 둔화가 금리 인하 기대를 키우며 성장주 강세',
        '예상치를 밑돈 물가 지표가 국채 금리를 끌어내렸고, 고밸류 기술주의 멀티플 확장 기대가 재점화됐습니다.',
        4,
        '["PPI", "FED", "RATES"]'::jsonb,
        3005,
        '예상 밑돈 PPI에 성장주 랠리 재점화',
        'CNBC',
        TIMESTAMPTZ '2026-03-17 10:10:00-04',
        'https://example.com/original-cnbc',
        'https://example.com/naver-cnbc'
    ),
    (
        2307,
        2104,
        NULL,
        '338e20f0-8d76-4a20-9e8f-7ad6105f61bf',
        1,
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        'HBM 수요 기대와 미국 반도체 강세가 국내 대형 메모리주로 전이되면서 지수 상단을 끌어올렸습니다.',
        5,
        '["KOSPI", "HBM", "FOREIGNERS"]'::jsonb,
        3006,
        '삼성전자·SK하이닉스 강세에 코스피 2,780선 회복',
        '한국경제',
        TIMESTAMPTZ '2026-03-17 14:10:00+09',
        'https://example.com/original-hk',
        'https://example.com/naver-hk'
    ),
    (
        2308,
        2104,
        NULL,
        'a2c7c439-c68e-4ef5-8c23-f267d87a4722',
        2,
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '업종 순환이 뚜렷해지면서 지수 상승과 체감 난이도 간 괴리가 커졌고, 시장은 대형주 중심 방어적 랠리 양상을 보였습니다.',
        3,
        '["KOSDAQ", "ROTATION", "LARGE_CAP"]'::jsonb,
        3007,
        '2차전지와 인터넷은 차익실현, 지수는 대형주 장세로 압축',
        '매일경제',
        TIMESTAMPTZ '2026-03-17 15:20:00+09',
        'https://example.com/original-mk',
        'https://example.com/naver-mk'
    );

UPDATE batch_job
SET
    page_id = CASE id
        WHEN 1001 THEN 2001
        WHEN 1004 THEN 2002
        ELSE page_id
    END,
    page_version_no = CASE id
        WHEN 1001 THEN 1
        WHEN 1004 THEN 1
        ELSE page_version_no
    END
WHERE id IN (1001, 1004);

COMMIT;
