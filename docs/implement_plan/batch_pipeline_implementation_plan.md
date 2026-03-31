# 배치 파이프라인 상세 구현 계획

## 1. 문서 목적

본 문서는 현재 구현된 배치 API/오케스트레이터 뼈대를 실제 데이터 수집, 정제, 클러스터링, 요약, 스냅샷 생성까지 확장하기 위한 상세 구현 계획을 정의한다.

본 문서는 아래 자료를 기준으로 한다.

- `docs/Product_Requirement_Document.md`
- `docs/api_spec_doc.md`
- `docs/postgresql_ddl_design.md`
- `docs/fastapi_postgresql_architecture.md`
- `db/schema_postgresql.sql`
- Naver Search News API 문서: <https://developers.naver.com/docs/serviceapi/search/news/news.md>
- yfinance PyPI 문서: <https://pypi.org/project/yfinance/>

본 문서는 PostgreSQL만 타깃으로 하며, MariaDB 설계는 범위에서 제외한다.

---

## 2. 구현 목표

이번 구현의 목표는 아래 4가지다.

1. `POST /stock/api/batch/market-daily`가 실제 배치 파이프라인을 시작할 수 있어야 한다.
2. 뉴스 수집, 정제, 클러스터링, 지수 수집, AI 요약, 스냅샷 생성이 단계별로 분리되어야 한다.
3. 배치 결과는 원천 계층과 스냅샷 계층에 모두 기록되어야 하며, 이후 조회 API가 즉시 활용 가능해야 한다.
4. 운영자는 `batch_job`, `batch_job_event`를 통해 성공/부분 실패/실패 원인을 추적할 수 있어야 한다.

이번 문서에서 확정하는 구현 기본 방침은 아래와 같다.

- 뉴스 수집원은 `Naver Search News API`
- 시장 지수 수집원은 `yfinance`
- LLM 호출은 `LangChain wrapper`를 통해 수행
- 환경변수는 모두 `.env`를 통해 로드
- 뉴스 검색어는 DB catalog 테이블에서 관리
- 뉴스 수집은 `sort=date` 기준으로 페이지네이션하며, 대상 `business_date`보다 오래된 기사 구간에 도달하면 중단

---

## 3. 외부 연동 제약 정리

### 3-1. Naver Search News API

Naver Search News API는 검색 기반 뉴스 수집 API로 사용한다.

핵심 제약:

- HTTP Method: `GET`
- Endpoint: `/v1/search/news.json`
- 인증 방식: 헤더 기반
  - `X-Naver-Client-Id`
  - `X-Naver-Client-Secret`
- 주요 파라미터
  - `query`
  - `display`
  - `start`
  - `sort`
- `sort`는 `sim` 또는 `date`
- 날짜 직접 필터 파라미터는 없으므로, 수집 후 발행 시각 기반으로 애플리케이션에서 `business_date` 적합 여부를 판정해야 한다.
- 키워드별 페이지네이션 상한과 중단 조건을 반드시 둬야 한다.

이번 구현에서는 아래 정책으로 고정한다.

- `sort=date`
- `display=100` 기본
- `start`를 증가시키며 수집
- 응답 기사 `pubDate`를 파싱하여 KST 기준 `business_date`를 계산
- 대상 날짜보다 오래된 기사 비중이 연속적으로 나타나면 해당 키워드 수집 종료
- 응답 원문은 `payload_json`으로 그대로 저장

### 3-2. yfinance

yfinance는 Yahoo Finance 공개 데이터를 가져오는 Python 라이브러리로 사용한다.

이번 구현에서는 아래 원칙으로 고정한다.

- 라이브러리 버전은 현재 최신 기준 `1.2.0`을 우선 사용
- 배치 시점에는 intraday가 아니라 일간 종가 기준 수집을 우선 구현
- 지수별 ticker는 코드 또는 seed 데이터로 관리 가능하지만, 초기 구현은 코드 상수로 시작
- 데이터 소스 특성상 일부 ticker 응답이 비거나 예외가 날 수 있으므로, 이는 `PARTIAL` 판정 후보로 처리

기본 ticker 매핑은 아래로 시작한다.

- US
  - `^GSPC`
  - `^IXIC`
  - `^DJI`
- KR
  - `^KS11`
  - `^KQ11`

---

## 4. 환경변수 설계

환경변수는 `app/core/settings.py`에서 `.env` 기반으로 로드한다.

필수 환경변수:

- `STOCKAPP_DATABASE_URL`
- `STOCKAPP_DATABASE_SCHEMA`
- `STOCKAPP_NAVER_CLIENT_ID`
- `STOCKAPP_NAVER_CLIENT_SECRET`
- `STOCKAPP_NAVER_NEWS_BASE_URL`
- `STOCKAPP_NAVER_NEWS_DISPLAY`
- `STOCKAPP_NAVER_NEWS_MAX_START`
- `STOCKAPP_NAVER_NEWS_MAX_PAGES_PER_KEYWORD`
- `STOCKAPP_YFINANCE_TIMEOUT_SECONDS`
- `STOCKAPP_LLM_PROVIDER`
- `STOCKAPP_LLM_MODEL`
- `STOCKAPP_LLM_TEMPERATURE`
- `STOCKAPP_LLM_MAX_RETRIES`

LangChain 하위 provider용 환경변수:

- `OPENAI_API_KEY`
- 기타 provider별 key는 추후 확장 가능하도록 wrapper에서 선택적으로 읽는다.

구현 원칙:

- `Settings`는 모든 외부 연동 기본값과 타임아웃을 포함
- provider별 SDK 초기화는 `core` 계층에서만 수행
- step 계층은 settings 직접 접근 대신 provider/repository를 통해 호출

권장 추가 파일:

- `app/core/llm.py`
- `app/batch/providers/news_provider.py`
- `app/batch/providers/market_index_provider.py`
- `app/batch/providers/llm_provider.py`

---

## 5. DB 확장안

### 5-1. 기존 테이블 유지

아래 기존 테이블은 그대로 사용한다.

- `batch_job`
- `batch_job_event`
- `news_article_raw`
- `news_article_processed`
- `news_article_raw_processed_map`
- `news_cluster`
- `news_cluster_article`
- `market_index_daily`
- `ai_summary`
- `market_daily_page`
- `market_daily_page_market`
- `market_daily_page_market_index`
- `market_daily_page_market_cluster`
- `market_daily_page_article_link`

### 5-2. 신규 테이블: `news_search_keyword`

뉴스 검색어를 DB에서 관리하기 위해 아래 테이블을 추가한다.

```sql
CREATE TABLE news_search_keyword (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    provider_name TEXT NOT NULL DEFAULT 'NAVER_NEWS_SEARCH',
    market_type market_type_enum NOT NULL,
    keyword TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    priority INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_news_search_keyword UNIQUE (provider_name, market_type, keyword),
    CONSTRAINT chk_news_search_keyword_priority_positive CHECK (priority > 0)
);

CREATE INDEX idx_news_search_keyword_active_priority
    ON news_search_keyword (provider_name, market_type, is_active, priority);
```

역할:

- 시장별 검색 키워드 관리
- 활성화/비활성화 제어
- 우선순위 기반 수집 순서 제어

초기 seed 예시:

- US
  - `미국 증시`
  - `나스닥`
  - `S&P 500`
  - `미국 금리`
  - `엔비디아`
- KR
  - `한국 증시`
  - `코스피`
  - `코스닥`
  - `원달러 환율`
  - `삼성전자`

초기 구현에서는 seed SQL 또는 migration seed로 삽입한다.

---

## 6. 배치 단계별 상세 구현

## 6-1. `create_job`

목적:

- 배치 실행 컨텍스트를 생성하고 운영 추적을 시작한다.

입력:

- `business_date`
- `force_run`
- `rebuild_page_only`
- `trigger_type`
- `triggered_by_user_id`

처리:

1. `business_date`가 없으면 KST 기준 현재 날짜 계산
2. 동일 날짜 `PENDING/RUNNING` job 존재 여부 검사
3. `force=false`이고 기존 page snapshot 존재 시 충돌 처리
4. `batch_job` row 생성
5. `CREATE_JOB` 이벤트 기록

출력:

- `BatchExecutionContext`

DB 반영:

- `batch_job`
- `batch_job_event`

LLM 사용 여부:

- 사용 안 함

실패 처리:

- 중복 실행: `BATCH_ALREADY_RUNNING`
- 기존 페이지 존재: `PAGE_ALREADY_EXISTS`

---

## 6-2. `collect_news`

목적:

- 활성 검색 키워드를 기준으로 원본 뉴스 데이터를 수집하여 `news_article_raw`에 저장한다.

입력:

- `business_date`
- `news_search_keyword`

처리:

1. `provider_name='NAVER_NEWS_SEARCH'`이고 `is_active=true`인 키워드 조회
2. 시장별/우선순위별로 키워드 순회
3. 키워드마다 Naver API 호출
4. 응답 기사 `pubDate`를 파싱하고 KST 기준 `business_date` 계산
5. 대상 날짜와 일치하는 기사만 저장 후보로 유지
6. `(provider_name, provider_article_key)` 유니크 충돌은 skip
7. `payload_json`에 원본 응답 아이템 저장
8. 수집 건수 누적

저장 기준:

- `provider_name`: `NAVER_NEWS_SEARCH`
- `provider_article_key`: 네이버 응답에서 안정적으로 식별 가능한 링크 기반 canonical key 생성
- `search_keyword`: 실제 호출한 키워드 저장
- `market_type`: keyword catalog의 소속 시장 사용

중단 조건:

- `start > STOCKAPP_NAVER_NEWS_MAX_START`
- 키워드별 페이지 수가 상한 도달
- 현재 페이지 기사 대부분이 대상 날짜보다 이전 날짜
- 응답 item이 비어 있음

출력:

- `raw_news_count`
- 키워드별 수집 로그

DB 반영:

- `news_article_raw`
- `batch_job_event`

LLM 사용 여부:

- 사용 안 함

테스트 포인트:

- 날짜 판정이 KST 기준으로 동작하는지
- 중복 기사 유니크 충돌 시 skip 되는지
- 비어 있는 응답에서 정상 종료하는지

---

## 6-3. `dedupe_articles`

목적:

- raw 기사들을 정규화하고 중복 제거하여 `news_article_processed`로 변환한다.

입력:

- `news_article_raw`

처리:

1. 제목, origin link, publisher, 발행 시각 정규화
2. dedupe 대상 canonical key 생성
3. `dedupe_hash` 계산
4. 동일 hash의 processed article이 있으면 재사용
5. 없으면 새 `news_article_processed` 생성
6. raw-processed mapping 저장

정규화 방침:

- 제목 HTML 태그 제거
- 공백 정리
- origin link canonicalization
- `naver_link`는 보조 링크로 유지

`dedupe_hash` 기본 규칙:

- `sha256(normalized_title + '|' + canonical_origin_link)`

출력:

- `processed_news_count`
- raw -> processed 매핑

DB 반영:

- `news_article_processed`
- `news_article_raw_processed_map`
- `batch_job_event`

LLM 사용 여부:

- 사용 안 함

테스트 포인트:

- 제목 정규화
- 링크 정규화
- 동일 기사 중복 제거
- mapping 저장

---

## 6-4. `build_clusters`

목적:

- processed 기사들을 이슈 단위로 묶고 대표 기사와 노출 순서를 확정한다.

입력:

- `news_article_processed`

처리 전략:

1. 1차 deterministic candidate grouping
   - 동일 시장
   - 동일 `business_date`
   - 제목 유사성/핵심 토큰 기준
2. 2차 LLM 기반 cluster merge/split
   - candidate 묶음을 의미 단위로 합치거나 분리
3. 대표 기사 확정
4. cluster title 생성
5. `summary_short` 초안 생성
6. cluster rank 계산
7. `news_cluster`, `news_cluster_article` 저장

LLM 사용 지점:

- candidate 그룹 의미 병합
- cluster title 생성
- `summary_short` 생성
- 대표 기사 우선순위 판단 보조

저장 원칙:

- 대표 기사는 반드시 `news_cluster_article` membership 안에 있어야 한다.
- `cluster_rank`는 시장별 중요도 순서로 부여한다.
- `cluster_uid`는 UUID로 생성

출력:

- `cluster_count`
- cluster별 대표 기사, rank, tags, summary

DB 반영:

- `news_cluster`
- `news_cluster_article`
- `batch_job_event`

테스트 포인트:

- deterministic grouping 결과
- LLM 응답 파싱
- representative membership FK 만족
- rank uniqueness 유지

---

## 6-5. `collect_market_indices`

목적:

- 미국/한국 대표 지수의 일간 지표를 수집한다.

입력:

- `business_date`

처리:

1. 시장별 ticker mapping 로드
2. yfinance 호출
3. 대상 일자의 OHLC/종가/변동률 추출
4. `market_index_daily` 저장
5. 일부 ticker 실패 시 partial candidate로 표시

기본 ticker:

- US
  - `^GSPC`
  - `^IXIC`
  - `^DJI`
- KR
  - `^KS11`
  - `^KQ11`

출력:

- 시장별 index row

DB 반영:

- `market_index_daily`
- `batch_job_event`

LLM 사용 여부:

- 사용 안 함

테스트 포인트:

- ticker 매핑
- 숫자 필드 변환
- 일부 ticker 실패 시 partial 기록

---

## 6-6. `generate_ai_summaries`

목적:

- 클러스터와 시장 상황을 기반으로 사용자 화면에 필요한 요약문을 생성한다.

입력:

- `news_cluster`
- `news_cluster_article`
- `news_article_processed`
- `market_index_daily`

생성 대상:

- 글로벌 헤드라인
- 시장별 요약
- 클러스터 카드 요약
- 클러스터 상세 분석 문단

DB 저장 타입:

- `GLOBAL_HEADLINE`
- `MARKET_SUMMARY`
- `CLUSTER_CARD_SUMMARY`
- `CLUSTER_DETAIL_ANALYSIS`

LLM 호출 계층:

- `app/core/llm.py` 또는 동등한 core wrapper
- step은 LangChain wrapper만 호출
- provider SDK 직접 접근 금지

LangChain wrapper 책임:

- 모델 초기화
- 공통 retry
- structured output 파싱
- provider 에러 표준화

저장 방식:

- 성공 시 `status=SUCCESS`
- fallback 문구 사용 시 `status=FALLBACK`, `fallback_used=true`
- 실패 시 `status=FAILED`, `error_message` 저장

출력:

- `ai_summary`

DB 반영:

- `ai_summary`
- `batch_job_event`

테스트 포인트:

- structured output parse
- fallback 저장
- summary type별 저장 결과

---

## 6-7. `build_page_snapshot`

목적:

- 원천 데이터를 읽어 사용자 화면용 snapshot aggregate를 생성한다.

입력:

- `batch_job`
- `news_cluster`
- `market_index_daily`
- `ai_summary`

처리:

1. 같은 `business_date` 내 최신 `version_no` 조회
2. 새 `version_no` 계산
3. `market_daily_page` 생성
4. `market_daily_page_market` 생성
5. `market_daily_page_market_index` 생성
6. `market_daily_page_market_cluster` 생성
7. `market_daily_page_article_link` 생성
   - 시장별 페이지 하단 링크 리스트용으로 클러스터 소속 기사 전체를 저장
   - 정렬은 `cluster_rank ASC -> article_rank ASC -> published_at DESC`

중요 원칙:

- snapshot은 원천 테이블을 읽고 snapshot 테이블에만 쓴다.
- 조회 API가 추가 조합 없이 1회 호출로 렌더링할 수 있어야 한다.
- 시장별 정렬 순서, cluster rank, article order는 이 시점에 확정한다.

LLM 사용 여부:

- 직접 호출 안 함
- 기존 `ai_summary` 결과를 읽어서 반영

출력:

- `page_id`
- `page_version_no`

DB 반영:

- `market_daily_page`
- `market_daily_page_market`
- `market_daily_page_market_index`
- `market_daily_page_market_cluster`
- `market_daily_page_article_link`
- `batch_job_event`

테스트 포인트:

- version 증가
- snapshot aggregate 생성
- article link/cluster/index 정렬

---

## 6-8. `finalize_job`

목적:

- 배치 종료 상태를 결정하고 집계/로그를 최종 반영한다.

처리:

1. raw/process/cluster count 집계 반영
2. `page_id`, `page_version_no` 연결
3. batch 상태 결정
4. `partial_message`, `error_code`, `error_message`, `log_summary` 저장
5. 종료 이벤트 기록

상태 결정 기본 정책:

- `FAILED`
  - 핵심 단계 중단
  - snapshot 미생성
  - DB write 실패
  - 외부 API 인증 오류 등으로 핵심 파이프라인 진행 불가
- `PARTIAL`
  - page snapshot은 생성됨
  - 일부 시장 데이터, 일부 지수, 일부 AI 결과가 누락됨
- `SUCCESS`
  - 필수 산출물이 모두 생성됨

DB 반영:

- `batch_job`
- `batch_job_event`

LLM 사용 여부:

- 사용 안 함

---

## 7. LLM 호출 필요 지점 정리

LLM 호출이 필요한 단계는 아래 2곳으로 고정한다.

### 7-1. `build_clusters`

용도:

- candidate cluster 병합/분리
- cluster title 생성
- `summary_short` 생성
- 대표 기사 우선순위 판단 보조

### 7-2. `generate_ai_summaries`

용도:

- 글로벌 헤드라인 생성
- 시장별 summary 생성
- cluster card summary 생성
- cluster detail analysis 생성

LLM 호출이 불필요한 단계:

- `create_job`
- `collect_news`
- `dedupe_articles`
- `collect_market_indices`
- `build_page_snapshot`
- `finalize_job`

핵심 원칙:

- LLM은 반드시 LangChain wrapper를 통해서만 호출
- 각 step은 provider SDK를 직접 import하지 않음
- prompt version, model name, fallback 사용 여부는 `ai_summary` 메타데이터에 저장

---

## 8. 구현 모듈 구조

권장 구조:

```text
app/
  core/
    settings.py
    timezone.py
    llm.py

  batch/
    orchestrators/
      market_daily.py
    providers/
      news_provider.py
      market_index_provider.py
      llm_provider.py
    steps/
      create_job.py
      collect_news.py
      dedupe_articles.py
      build_clusters.py
      collect_market_indices.py
      generate_ai_summaries.py
      build_page_snapshot.py
      finalize_job.py
    policies/
      batch_status_policy.py
      page_status_policy.py
      partial_failure_policy.py

  db/
    repositories/
      batch_job_repo.py
      article_repo.py
      cluster_repo.py
      index_repo.py
      ai_summary_repo.py
      page_snapshot_repo.py
      keyword_repo.py
```

구현 원칙:

- step은 orchestration만 담당
- provider는 외부 API 호출만 담당
- repository는 SQL만 담당
- 정책은 상태 판정만 담당

---

## 9. 실패 처리 및 재시도 정책

기본 원칙:

- 외부 API 실패는 가능한 한 step 내부에서 격리
- 치명적 오류가 아니면 `PARTIAL` 후보로 누적
- snapshot 생성 불가 수준이면 `FAILED`

정책:

- Naver 인증 실패
  - 즉시 `FAILED`
- 일부 keyword 호출 실패
  - 이벤트 `WARN`
  - 수집 계속
- yfinance 일부 ticker 실패
  - 이벤트 `WARN`
  - `PARTIAL` 후보
- LLM 일부 summary 실패
  - fallback 문구 사용
  - `FALLBACK` 저장
  - 전체 배치는 `PARTIAL` 가능
- DB constraint 위반
  - 원인에 따라 skip 또는 `FAILED`

재시도 방침:

- 네트워크/일시 오류는 provider 계층에서 제한적 retry
- 배치 전체 재시도는 기존 API의 `force` 또는 재실행 요청으로 처리

---

## 10. 테스트 및 검증 계획

### 10-1. 단위 테스트

- KST `business_date` 계산
- Naver 응답 파싱
- dedupe hash 생성
- 대표 기사 선택 규칙
- batch status policy
- LangChain wrapper 응답 파싱

### 10-2. Repository 테스트

- keyword 조회
- raw article insert
- processed article dedupe
- cluster insert 및 representative membership
- market index insert
- ai_summary insert
- page snapshot version 증가

### 10-3. 배치 step 테스트

- `collect_news`
- `dedupe_articles`
- `build_clusters`
- `collect_market_indices`
- `generate_ai_summaries`
- `build_page_snapshot`
- `finalize_job`

### 10-4. 통합 테스트

Happy path:

1. seed keyword
2. Naver mock 응답 수집
3. processed article 생성
4. cluster 생성
5. index 수집
6. ai_summary 생성
7. snapshot 생성
8. batch 종료

예외 시나리오:

- Naver 인증 실패
- 일부 keyword 실패
- 일부 ticker 누락
- 일부 AI fallback
- snapshot build 실패

### 10-5. 실환경 smoke test

- `.env` 로딩 확인
- 실제 Postgres 연결 확인
- 배치 실행 후 `batch_job_event` 누적 확인
- 생성된 page가 조회 API에서 바로 노출되는지 확인

---

## 11. 구현 순서 제안

구현 우선순위는 아래 순서로 고정한다.

### 1단계

- `.env` 설정 확장
- settings 정리
- `news_search_keyword` DDL 추가
- keyword repository 추가

### 2단계

- Naver provider 구현
- `collect_news` 실제 저장 구현
- `news_article_raw` 테스트 추가

### 3단계

- dedupe repository/step 구현
- `news_article_processed`, mapping 저장 구현

### 4단계

- LangChain wrapper 구현
- `build_clusters` 구현

### 5단계

- yfinance provider 구현
- `collect_market_indices` 구현

### 6단계

- `generate_ai_summaries` 구현
- `ai_summary` 저장 구현

### 7단계

- `build_page_snapshot` 구현
- snapshot aggregate 저장 구현

### 8단계

- `finalize_job` 정책 정교화
- batch 상세 이벤트 응답 확장
- 실DB smoke test

---

## 12. 확정된 기본 가정

- 뉴스 provider는 Naver Search News API로 고정한다.
- 뉴스 검색어는 DB에서 관리한다.
- LLM 호출은 LangChain wrapper를 통해 공통 진입점으로만 수행한다.
- 실제 LLM provider 선택은 `.env` 설정 기반으로 한다.
- 지수 데이터는 yfinance를 통해 수집한다.
- yfinance 일부 누락은 `PARTIAL` 정책으로 처리 가능하다.
- 뉴스 수집은 `sort=date` 기반으로 수행한다.
- 운영용 키워드 관리 UI는 이번 범위에 포함하지 않는다.
