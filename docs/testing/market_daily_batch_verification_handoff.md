# Market Daily Batch 검증 인수인계

작성일: 2026-07-28 (Asia/Seoul)  
대상: `POST /stock/api/batch/market-daily`

## 1. 목적과 현재 결론

첨부된 운영 화면의 실패는 다음 순서로 재현·확인되었다.

1. 배포 DB에 활성 Naver 검색어가 없음
2. 뉴스 원문 0건
3. 가공 기사 0건
4. 클러스터 0건
5. 시장 지수만 독립적으로 5건 수집
6. 페이지 생성 원천 누락으로 `SNAPSHOT_SOURCE_MISSING`
7. 최종 작업 상태 `FAILED`

이 흐름은 LLM 호출 전에 발생하므로, 첨부 화면 자체는 LLM API 오류가 아니다.

정적 분석과 최초 E2E에서 추가로 확인된 가장 앞단의 차단 오류는
`batch_job.triggered_by_user_id`의 PostgreSQL `UUID` 타입과 JWT `sub` 문자열
바인딩 불일치였다. ADMIN 요청이 작업 생성 전에 HTTP 500으로 종료되었으며,
PostgreSQL 오류는 다음과 같았다.

```text
column "triggered_by_user_id" is of type uuid
but expression is of type character varying
```

현재 변경본은 이 오류와 배치 파이프라인의 관련 결함을 수정한다.

## 2. 구현된 수정

### 2.1 DB 계약과 배포 데이터

- `batch_job.triggered_by_user_id`를 `TEXT`로 변경
  - UUID 형태와 `USER-0001` 같은 일반 JWT subject를 모두 보존
- 신규 설치용 기본 활성 검색어 추가
  - US: `미국 증시`
  - KR: `코스피`
- 기존 `NAVER_NEWS_SEARCH` provider 값을 런타임 상수인 `NAVER_NEWS`로 병합
- 기사 dedupe 키를 전역 유일 키에서 날짜별 유일 키로 변경
  - 이전: `UNIQUE(dedupe_hash)`
  - 현재: `UNIQUE(business_date, dedupe_hash)`
- 기존 DB용 멱등 마이그레이션 추가
  - `db/migrations/20260728_01_batch_job_trigger_subject_text.sql`
  - `db/migrations/20260728_02_naver_news_keyword_seeds.sql`
  - `db/migrations/20260728_03_news_article_processed_date_dedupe.sql`

마이그레이션 03은 하나의 명시적 트랜잭션으로 실행된다. 날짜별 중복 데이터가
이미 존재하면 SQLSTATE `23505`와 함께 정리 필요성을 알리고 중단한다. 중단된
과거 concurrent index가 남아 있어도 제거 후 정상 constraint를 다시 생성한다.

### 2.2 배치 오류 분류와 진단

- 활성 검색어 없음: `NEWS_KEYWORDS_NOT_CONFIGURED`
- Naver 자격 증명 미설정: `NAVER_NOT_CONFIGURED`
- Naver HTTP 401/403: `NAVER_AUTH_FAILED`
- 일시적 검색어별 실패:
  - 전체 배치를 즉시 실패시키지 않음
  - WARN 이벤트와 구체적인 partial reason 기록
- yfinance 일부 ticker 실패:
  - 누락 ticker를 WARN/PARTIAL로 반영
- 클러스터/요약 LLM fallback:
  - fallback count와 구체적인 진단 이벤트 기록
  - 작업과 페이지를 일관되게 `PARTIAL`로 처리
- 실패한 단계가 context 수치를 변경한 뒤 롤백되더라도, 마지막으로 commit된
  context만 FAILED 작업에 기록

### 2.3 카운트와 상태 일관성

- 재시도/강제 실행 시 새로 INSERT된 뉴스 수가 아니라 해당 영업일에 실제로
  저장된 전체 raw 기사 수를 기록
- `fallback_count`, warning, partial reason을 페이지 상태에도 반영
- 페이지 생성 전에 `partial_message`를 계산하여 페이지와 작업의 상태/메시지가
  일치하도록 변경
- yfinance timeout 설정을 실제 각 ticker 호출에 적용
- NaN/Infinity 가격은 DB에 저장하지 않도록 방어

### 2.4 `rebuildPageOnly`

- 기존 페이지가 있으면 `force=false`여도 재빌드 허용
- 기존 페이지가 없거나 저장된 snapshot 원천이 없으면 명시적으로 거부
- 재빌드는 mutable 원천 테이블을 다시 읽지 않고, 직전 persisted page snapshot과
  다음 자식 행을 그대로 새 버전으로 복제
  - market
  - index
  - cluster
  - article link
- 재빌드 trigger type은 `ADMIN_REBUILD`
- 재빌드 중 뉴스/Naver/yfinance/Gemini 호출 없음

## 3. 변경 파일

이번 배치 작업 범위의 주요 변경 파일은 다음과 같다.

```text
app/batch/exceptions.py
app/batch/orchestrators/market_daily.py
app/batch/providers/market_index_provider.py
app/batch/steps/build_clusters.py
app/batch/steps/build_page_snapshot.py
app/batch/steps/collect_market_indices.py
app/batch/steps/collect_news.py
app/batch/steps/finalize_job.py
app/batch/steps/generate_ai_summaries.py
app/db/repositories/batch_job_repo.py
app/db/repositories/news_article_processed_repo.py
app/db/repositories/news_article_raw_repo.py
app/db/repositories/page_snapshot_repo.py
app/domains/batches/service.py
db/schema_postgresql.sql
db/migrations/20260728_01_batch_job_trigger_subject_text.sql
db/migrations/20260728_02_naver_news_keyword_seeds.sql
db/migrations/20260728_03_news_article_processed_date_dedupe.sql
tests/batch/test_build_page_snapshot_rebuild.py
tests/batch/test_market_index_provider.py
tests/batch/test_pipeline_failure_diagnostics.py
tests/db/test_migrations_postgresql.py
tests/db/test_schema_migrations.py
```

그 외 기존 테스트 파일에는 관련 회귀 assertion과 fake repository 계약이
추가되었다. 작업 시작 전부터 존재하던 Dockerfile, README, health, 기타 문서
변경은 이 작업에서 수정하거나 되돌리지 않았다.

## 4. 완료된 검증

### 4.1 정적 분석

엔드포인트에서 8개 배치 단계, repository, schema, 오류 상태 정책까지 추적했다.
첨부 화면의 실행 흐름과 검색어 미설정 원인을 코드 경로로 확인했다.

### 4.2 자동 테스트

최종 결합 상태에서 독립 QA가 실행한 결과:

```text
uv run pytest
180 passed, 4 skipped, 2 warnings

uv run ruff check app tests
PASS

git diff --check
PASS
```

추가된 주요 회귀 테스트:

- UUID/일반 문자열 JWT subject 저장
- 신규 schema 검색어 seed
- legacy provider 병합
- 동일 기사 hash의 서로 다른 영업일 저장
- 빈 검색어/Naver 미설정/Naver 인증 오류의 typed failure
- 일시적 검색어 실패와 일부 ticker 실패의 PARTIAL 처리
- raw 기사 재집계
- LLM fallback 진단 및 상태 일치
- 실패 단계의 미커밋 context 누출 방지
- yfinance timeout, 휴장일 fallback, NaN/Infinity 처리
- persisted snapshot 기반 재빌드와 `ADMIN_REBUILD`

### 4.3 실제 PostgreSQL 마이그레이션

PostgreSQL 16에서 다음을 확인했다.

- 신규 schema 적용 성공
- legacy schema에서 3개 migration 순서 적용 성공
- 전체 migration 재실행 성공
- migration 03 파일을 한 번에 실행하고 다시 실행해도 성공
- `triggered_by_user_id`가 `text`
- legacy UUID subject 보존
- 일반 문자열 subject 저장 가능
- `NAVER_NEWS_SEARCH` 잔여 행 0건
- US/KR 필수 기본 검색어 존재
- `(business_date, dedupe_hash)` unique constraint valid/unique
- 기존 전역 dedupe constraint 제거
- 동일 hash를 서로 다른 두 날짜에 저장 가능
- 사전에 날짜별 중복이 있으면 migration이 명시적으로 중단
- invalid index가 남은 legacy 상태에서 복구 가능

### 4.4 실제 API E2E에서 확인된 범위

수정 전:

- bearer token 없음: HTTP 401
- USER token: HTTP 403
- ADMIN token: UUID/VARCHAR 불일치로 HTTP 500
- 작업 행 생성 안 됨

DB 계약 수정 및 migration 적용 후:

- ADMIN 요청으로 작업 생성 성공
- job 7, 영업일 `2026-07-28`
- background orchestrator 실행 및 terminal 상태 도달
- 즉, 최초 HTTP 500 차단 오류는 제거됨

그러나 로컬 사내 TLS proxy 환경에서 Naver/yfinance 인증서 검증이 실패하여
뉴스 이후의 happy-path E2E는 완료하지 못했다.

```text
Naver: ConnectError / CERTIFICATE_VERIFY_FAILED
yfinance: CertificateVerifyError
job 7: FAILED / SNAPSHOT_SOURCE_MISSING
```

뉴스/클러스터가 없었으므로 Gemini는 호출되지 않았다. 따라서 확인된 LLM API
오류는 현재 없다.

재빌드 수정 전 진단 실행:

- job 8, 영업일 `2026-03-17`
- provider 호출 없이 snapshot 단계까지 진입
- 기존 구현은 ai_summary 원천 부재를 이유로 `SNAPSHOT_SOURCE_MISSING`
- 수정 후 persisted child snapshot 복제 방식으로 변경하고 자동 회귀 테스트 통과
- 수정 후 실제 API 재빌드는 아직 수행하지 않음

### 4.5 로컬 DB에 남은 QA 데이터

QA가 생성한 데이터:

- batch job: 7, 8
- batch job event: ID 9~64
- job 7/8로 생성된 raw/processed/cluster/index/summary/page 행: 0

개인 PC 검증 전 필요하면 별도 판단 후 정리한다. 자동으로 삭제하지 않았다.

## 5. 수행하지 않았거나 남은 검증

### 5.1 반드시 개인 PC에서 수행할 항목

1. Naver live 수집 성공
2. 기사 본문 live fetch 성공
3. yfinance 5개 ticker live 수집 성공
4. Gemini live 호출 성공
5. raw → processed → cluster → index → summary → page 전체 happy path
6. 최종 job/page 상태 및 카운트 일치
7. 실제 API를 통한 수정 후 `rebuildPageOnly` 성공과 version 증가
8. 프런트 Batch Operations 화면에서 목록/상세/partial message 확인

### 5.2 인증서 관련

현재 장비에서는 TLS proxy가 동적으로 발급한 인증서를 Python/certifi가
신뢰하지 못했다. 시스템 curl은 macOS Keychain을 사용해 같은 호스트를
검증했지만 Python HTTP client와 yfinance/curl_cffi는 실패했다.

사용자 요청에 따라 인증서 코드는 수정하지 않았다. 개인 PC가 공용 인증서
체인을 직접 신뢰하는 환경이면 별도 조치 없이 재검증한다. 동일 오류가 발생하면
TLS 검증을 끄지 말고 승인된 CA bundle 또는 조직 표준 설정을 사용한다.

`http://127.0.0.1` 허용은 검색 API의 호출자/IP 정책이며 서버 인증서 신뢰 문제와
별개다. 이 이유로 애플리케이션의 production CORS 정책은 변경하지 않았다.

### 5.3 LLM 검증

Gemini 설정은 로드되었지만 live 호출까지 도달하지 못했다.

개인 PC E2E에서 LLM 오류가 발생하면 다음을 별도로 기록한다.

- provider
- model
- error class
- 안전하게 마스킹한 error message
- 발생 step (`BUILD_CLUSTERS` 또는 `GENERATE_AI_SUMMARIES`)
- fallback 생성 여부
- job/page가 모두 `PARTIAL`인지 여부

API key, JWT, 전체 요청 payload는 문서나 로그에 복사하지 않는다.

### 5.4 포맷 검사

```text
uv run ruff format --check app tests
FAIL
```

23개 파일이 포맷 대상이며, 이 중 11개는 이번 작업 전부터 존재한 formatting
debt다. 작업 트리 변경 파일 중 배치 관련 포맷 대상도 남아 있다. lint는 통과한다.
사용자 변경과 혼합된 파일이 있으므로 자동 전체 포맷은 수행하지 않았다.

### 5.5 독립 재리뷰

첫 독립 코드 리뷰가 찾은 다음 3개 HIGH 항목은 후속 수정과 회귀 테스트를
완료했다.

- 실패 단계의 미커밋 context 누출
- persisted snapshot이 아닌 live 원천을 사용한 재빌드
- interrupted concurrent index에 취약한 migration

추가 MEDIUM 항목인 `ADMIN_REBUILD`, `NAVER_NOT_CONFIGURED`, test fake용 production
fallback도 수정했다. 다만 열린 파일 한도 문제로 후속 수정 후 별도 리뷰 에이전트의
최종 재리뷰는 실행하지 않았다.

### 5.6 구조적 운영 리스크

이 문서 작성 당시에는 FastAPI in-process `BackgroundTasks`로 실행되어 프로세스
종료 시 RUNNING 상태가 남는 리스크가 있었다. 이후 PostgreSQL durable queue와
별도 worker가 추가되어 `SKIP LOCKED` claim, heartbeat/lease, 만료 lease 복구,
step checkpoint 재개를 수행한다. 현재 검증은 API 프로세스와
`python -m app.batch.worker` 프로세스를 함께 실행하는 구성을 기준으로 한다.

## 6. 개인 PC 검증 절차

### 6.1 사전 준비

```bash
cd {PROJECT_ROOT}
uv sync --dev
```

다음을 준비한다.

- 별도 테스트 PostgreSQL 또는 검증 가능한 DB 백업
- `STOCKAPP_DATABASE_URL`
- `STOCKAPP_DATABASE_SCHEMA=stock`
- Naver client ID/secret
- Gemini API key
- JWT issuer/audience/secret
- 실제 ADMIN bearer token

운영 DB에 바로 적용하지 말고 백업 또는 disposable DB에서 먼저 검증한다.

### 6.2 migration 적용

`PG_DSN`에는 `postgresql://...` 형식의 psql용 DSN을 사용한다.
SQLAlchemy의 `postgresql+psycopg://...` 문자열을 그대로 사용하지 않는다.

```bash
psql "$PG_DSN" -v ON_ERROR_STOP=1 \
  -f db/migrations/20260728_01_batch_job_trigger_subject_text.sql

psql "$PG_DSN" -v ON_ERROR_STOP=1 \
  -f db/migrations/20260728_02_naver_news_keyword_seeds.sql

psql "$PG_DSN" -v ON_ERROR_STOP=1 \
  -f db/migrations/20260728_03_news_article_processed_date_dedupe.sql
```

동일 명령을 한 번 더 실행하여 멱등성을 확인한다.

### 6.3 migration 결과 확인

```sql
SELECT data_type
FROM information_schema.columns
WHERE table_schema = 'stock'
  AND table_name = 'batch_job'
  AND column_name = 'triggered_by_user_id';

SELECT provider_name, market_type, keyword, is_active, priority
FROM stock.news_search_keyword
ORDER BY market_type, priority, id;

SELECT conname, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = 'stock.news_article_processed'::regclass
  AND contype = 'u';

SELECT business_date, dedupe_hash, count(*)
FROM stock.news_article_processed
GROUP BY business_date, dedupe_hash
HAVING count(*) > 1;
```

성공 기준:

- `triggered_by_user_id`: `text`
- 활성 `NAVER_NEWS` US/KR 검색어 존재
- `NAVER_NEWS_SEARCH` 없음
- `UNIQUE (business_date, dedupe_hash)` 존재
- 날짜별 중복 조회 결과 0건

### 6.4 자동 테스트

```bash
uv run pytest
uv run ruff check app tests
git diff --check
```

현재 기준 기대 결과:

```text
pytest: 180 passed, 4 skipped
ruff check: PASS
git diff --check: PASS
```

포맷은 변경 파일을 확인한 뒤 사용자 변경과 분리하여 선택적으로 수행한다.

```bash
uv run ruff format --check app tests
```

### 6.5 API 실행

```bash
uv run fastapi run --host 127.0.0.1 --port 8000
```

다른 터미널에서 health를 확인한다.

```bash
curl -sS http://127.0.0.1:8000/stock/api/health
```

ADMIN token은 로그인/인증 서비스에서 정상 발급한 값을 사용한다.

```bash
export ADMIN_TOKEN='<redacted>'
export BUSINESS_DATE='YYYY-MM-DD'

curl -sS -X POST \
  'http://127.0.0.1:8000/stock/api/batch/market-daily' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{
    \"businessDate\": \"$BUSINESS_DATE\",
    \"force\": false,
    \"rebuildPageOnly\": false
  }"
```

응답의 `data.jobId`를 기록한다.

```bash
export JOB_ID='<job-id>'

curl -sS \
  "http://127.0.0.1:8000/stock/api/batch/jobs/$JOB_ID" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

terminal status가 될 때까지 합리적인 간격으로 수동 조회한다.

### 6.6 DB E2E 확인

```sql
SELECT
    id,
    business_date,
    status,
    trigger_type,
    raw_news_count,
    processed_news_count,
    cluster_count,
    page_id,
    page_version_no,
    partial_message,
    error_code,
    error_message,
    log_summary
FROM stock.batch_job
WHERE id = :job_id;

SELECT step_code, level, message, context_json, created_at
FROM stock.batch_job_event
WHERE batch_job_id = :job_id
ORDER BY id;

SELECT count(*) FROM stock.news_article_raw
WHERE business_date = :business_date;

SELECT count(*) FROM stock.news_article_processed
WHERE business_date = :business_date;

SELECT count(*) FROM stock.news_cluster
WHERE business_date = :business_date;

SELECT count(*) FROM stock.market_index_daily
WHERE business_date = :business_date;

SELECT status, count(*), array_agg(error_message)
FROM stock.ai_summary
WHERE batch_job_id = :job_id
GROUP BY status;

SELECT id, version_no, status, raw_news_count, processed_news_count,
       cluster_count, partial_message
FROM stock.market_daily_page
WHERE batch_job_id = :job_id;
```

happy-path 성공 기준:

- 작업이 RUNNING에 머물지 않고 terminal 상태 도달
- raw/processed/cluster가 0보다 큼
- 시장 지수는 정상 거래 데이터 범위에서 기대 ticker 수와 일치
- page 및 US/KR page market 생성
- page/job 카운트와 상태 일치
- LLM 오류가 없으면 fallback 관련 WARN 없음
- 일부 provider/LLM fallback이면 job/page 모두 `PARTIAL`
- `PARTIAL`이면 `partial_message`가 비어 있지 않음
- fatal error면 구체적인 `error_code`와 event context 존재

### 6.7 재빌드 검증

정상 또는 PARTIAL source page가 생성된 같은 날짜로 실행한다.

```bash
curl -sS -X POST \
  'http://127.0.0.1:8000/stock/api/batch/market-daily' \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d "{
    \"businessDate\": \"$BUSINESS_DATE\",
    \"force\": false,
    \"rebuildPageOnly\": true
  }"
```

성공 기준:

- trigger type `ADMIN_REBUILD`
- 새 page version = 이전 version + 1
- Naver/yfinance/Gemini provider event 없음
- source와 새 페이지의 market/index/cluster/article-link 내용과 순서가 동일
- 새 identity와 batch linkage만 다름
- counts/status/partial message가 source와 동일

### 6.8 LLM 오류 확인

```sql
SELECT step_code, level, message, context_json
FROM stock.batch_job_event
WHERE batch_job_id = :job_id
  AND (
    step_code IN ('BUILD_CLUSTERS', 'GENERATE_AI_SUMMARIES')
    OR context_json::text ILIKE '%BatchLlmProvider%'
  )
ORDER BY id;
```

LLM 오류가 있으면 코드를 임의로 변경하기 전에 provider/model/error와 fallback
상태를 별도로 공유한다.

## 7. 열린 파일 오류 정리

작업 중 `Too many open files (os error 24)`가 발생했다.

확인 당시:

- shell soft limit: 256
- Codex 열린 파일: 241
- 서브에이전트가 남긴 것으로 보이는 Pencil MCP process: 11개
- 잔여 FastAPI/pytest process: 없음

조치:

- 모든 서브에이전트 작업 중지
- 11개 Pencil MCP process 종료
- Codex 열린 파일 241 → 203
- 추가 서브에이전트 생성 중단

향후 동일 증상이 있으면 새 테스트를 시작하기 전에 잔여 FastAPI/pytest/MCP
프로세스를 먼저 확인한다.

## 8. 2026-07-28 개인 PC 재개 결과

회사 PC에서 중단된 절차를 개인 PC에서 재개했다. 사용자 제공 DB URL은
프로세스 범위에서만 `STOCKAPP_DATABASE_URL`로 매핑했고, 로컬 검증용 JWT
설정과 토큰도 프로세스 메모리에만 두었다. 서버와 모든 API 요청은 Naver
검색 API 제약에 맞춰 `http://127.0.0.1:8000`을 사용했다. 검증 종료 후
서버와 임시 인증 환경을 제거했으며, 생성된 배치/페이지 데이터는 삭제하지
않았다.

### 8.1 provider smoke 결과

- Naver 검색: certifi 검증을 유지한 실요청 성공, 1페이지 100건 반환
- yfinance: 5개 ticker 모두 성공
  - US `^GSPC`, `^IXIC`, `^DJI` 실제 source date: `2026-07-27`
  - KR `^KS11`, `^KQ11` 실제 source date: `2026-07-28`
- Gemini: 현재 LangChain 응답의 `content`가 문자열이 아니라
  `[{type, text, extras}]` 형태일 수 있음을 확인

Gemini structured content를 `str(response.content)`로 변환해 JSON 파싱하던
문제를 `GeminiJsonClient`에서 실제 text block을 추출하도록 수정했다.
문자열, fenced JSON, 실제 `AIMessage`, 복수/mixed block, 빈/지원하지 않는
content에 대한 회귀 테스트를 추가했다. 최소 실요청은 수정 후 정상 JSON
응답을 반환했다.

### 8.2 full live batch

`2026-07-28` 기준 full batch는 job 1, page 1(version 1)을 생성했다.

- terminal status: `PARTIAL`
- duration: 403초
- raw / processed / cluster: `1318 / 1265 / 153`
- page와 job의 status/count/partial message 일치
- page child: market 2, index 5, cluster 153, article link 1265
- article content provider fallback WARN: 4건
- cluster enrichment fallback: 76건
  - `LlmTimeoutError`: 5건
  - Gemini `RESOURCE_EXHAUSTED` 429: 71건
- `ai_summary`: 총 309건 모두 `FALLBACK`
  - cluster card 153, cluster detail 153, global headline 1, market summary 2

Gemini 429의 직접 원인은 사용 중인 free-tier 모델의 분당 요청 한도
15건이다. 파이프라인은 의도한 fallback 경로로 페이지까지 생성했지만,
외부 quota가 유지되는 동안 LLM 전체 success 상태는 재현할 수 없다.

### 8.3 rebuild-only

같은 날짜의 `rebuildPageOnly=true` 실행은 job 2, page 2(version 2)를
생성했다.

- trigger type: `ADMIN_REBUILD`
- terminal status: `PARTIAL`
- duration: 0초
- raw / processed / cluster: `1318 / 1265 / 153`
- source page와 status/count/partial message 일치
- 새 `ai_summary` row: 0건
- provider context event: 0건
- Naver 수집, cluster 생성/fallback, AI provider/fallback 이벤트 없음
  (각 단계의 공통 started/completed 이벤트만 기록)
- 양쪽 child count: `2 / 5 / 153 / 1265`
- index, cluster, article-link의 semantic hash와 순서 일치
- market summary/analysis/count/order/partial/metadata 일치
- market `last_updated_at`만 rebuild 시각으로 갱신

### 8.4 최종 로컬 검증

```text
uv run pytest                         186 passed, 4 skipped
uv run ruff check app tests           passed
ruff format --check (변경 파일 2개)    passed
git diff --check                      passed
```

전체 `ruff format --check app tests`는 이번 변경과 무관한 기존 23개 파일의
format debt 때문에 실패한다.
