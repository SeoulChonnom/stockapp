# 서비스 사용성·잠재 오류 분석 보고서

- 분석일: 2026-07-14
- 분석 범위: `app/**` 전체 (배치 파이프라인, 읽기 API, 코어/DB/설정/인증), `db/schema_postgresql.sql`, `Dockerfile`, `docs/*`
- 분석 방법: 영역별 3개 서브에이전트(배치·LLM / 도메인 API / 인프라·DB·인증) 병렬 정밀 분석 후 교차 검증. 심각도 상위 항목은 실제 코드 재확인 및 컴파일 검사로 직접 검증함.

---

## 1. 서비스 동작 요약

### 배치 파이프라인 (일일 수집)

`POST /stock/api/batch/market-daily` (ADMIN 권한) → `batch_job` 행 생성(RUNNING) → FastAPI `BackgroundTasks`로 같은 프로세스 안에서 8개 스텝을 순차 실행:

1. `CreateJobStep` — 컨텍스트 초기화
2. `CollectNewsStep` — 네이버 뉴스 검색 API 수집
3. `DedupeArticlesStep` — 중복 제거 + 기사 본문 크롤링
4. `BuildClustersStep` — LLM 기반 클러스터링 (실패 시 토큰 교집합 폴백)
5. `CollectMarketIndicesStep` — yfinance 지수 수집
6. `GenerateAiSummariesStep` — Gemini로 글로벌/시장/클러스터 요약 생성
7. `BuildPageSnapshotStep` — 페이지 스냅샷 작성
8. `FinalizeJobStep` — SUCCESS / PARTIAL / FAILED 확정

스텝마다 커밋하며, 예외 발생 시 롤백 후 잡을 FAILED로 마킹.

### 읽기 API

| Method | Path | 설명 |
|---|---|---|
| GET | `/stock/api/pages/daily/latest` | 최신 일일 페이지 |
| GET | `/stock/api/pages/daily?businessDate=&versionNo=` | 날짜/버전별 페이지 |
| GET | `/stock/api/pages/{pageId}` | 페이지 상세 |
| GET | `/stock/api/pages/archive?...` | 아카이브 목록 (페이지네이션) |
| GET | `/stock/api/news/clusters/{clusterId}` | 클러스터 상세 |
| GET/POST | `/stock/api/batch/...` | 배치 트리거/상태 조회 |

전 엔드포인트 JWT 인증(USER/ADMIN) 필요, `ApiSuccess`/`ApiError` 봉투 응답.

---

## 2. 잠재 오류 가능성 — 심각도: 높음

### 2-1. 배치 도중 프로세스 재시작 시 `RUNNING` 고착 — 복구 수단 없음

- 위치: `app/domains/batches/router.py:48-63`, `app/domains/batches/service.py:58-101`
- 배치가 별도 워커 큐 없이 FastAPI `BackgroundTasks`(요청을 받은 ASGI 워커 프로세스 내부)로 실행된다. 배포·크래시로 프로세스가 죽으면 해당 `batch_job`은 영원히 `RUNNING`으로 남는다.
- `uq_batch_job_one_active_per_day` 부분 유니크 인덱스와 `has_active_job_for_business_date` 체크 때문에 **같은 날짜 재트리거도 차단**되어, 운영자가 DB를 직접 수정해야만 복구된다. 취소/강제 리셋/재시도 API가 없다.
- 정황 증거: `tests/integration/test_collect_news_live.py:85-111`이 테스트 셋업에서 stale RUNNING job을 SQL로 직접 정리한다 — 이미 실무에서 겪고 있는 문제로 보인다.
- **개선**: (a) `started_at` 기준 임계치 초과 RUNNING 잡을 FAILED로 마킹하는 워치독 또는 기동 시 정리 로직, (b) 관리자용 강제 리셋 엔드포인트, (c) 장기적으로 전용 작업 큐(arq/Celery) 도입.

### 2-2. LLM 응답 파싱이 try 블록 밖 — 클러스터 1개의 이상 응답이 배치 전체를 FAILED로

- 위치: `app/batch/steps/build_clusters.py:250` (직접 확인)
```python
    except Exception as exc:
        fallback['error_context'] = _serialize_exception(exc)
        return fallback

    representative_index = int(result.get('representative_article_index', 0) or 0)
```
- `enrich_cluster` 호출은 보호되지만 결과 파싱(`int(...)`)과 이후 dict 구성은 보호 범위 밖이다. Gemini가 `representative_article_index: "N/A"` 같은 값을 반환하면 `ValueError`가 오케스트레이터 최상위까지 전파되어 **다른 클러스터가 모두 정상이어도 하루 배치 전체가 FAILED + 롤백**된다.
- 대조적으로 `generate_ai_summaries.py`의 요약 생성 함수들은 파싱까지 try 안에 두고 폴백 처리한다 — 이 함수만 예외적으로 취약하다.
- **개선**: try 범위를 파싱·리턴 구성까지 확장하거나 `int(...)`를 개별 try로 감싸 0 폴백.

### 2-3. 공통 예외 핸들러 부재 — 미처리 예외가 문서화된 에러 봉투를 깨뜨림

- 위치: `app/core/exceptions.py:37-55`, `app/domains/archive/router.py:31`
- 예외 핸들러가 `AppError`와 `RequestValidationError` 두 가지뿐이다. 아카이브의 `status` 쿼리 파라미터는 `str | None`로 enum 검증 없이 raw SQL에서 `CAST(:status AS page_status_enum)`되므로, `status=FOO` 요청 한 번에 DB 예외 → FastAPI 기본 500(비봉투 응답)이 노출된다.
- `docs/api_spec_doc.md`는 모든 실패 응답이 동일 봉투 구조라고 명시하지만 이 경로에서 계약이 깨진다. 어셈블러의 `row['field']` 직접 키 접근도 데이터 정합성 붕괴 시 같은 경로로 이어진다.
- **개선**: `status`를 `Literal['READY','PARTIAL','FAILED']`로 선언해 422 처리 + 최후 방어용 일반 `Exception` 핸들러 추가.

### 2-4. 시크릿 관리 — `.env` 평문 키 + DB 접속정보 하드코딩 폴백 + 기동 시 검증 부재

- 위치: `.env` (저장소 루트), `app/core/settings.py:19-21` (직접 확인)
```python
database_url: str = Field(
    default='postgresql+psycopg://mcp_doc:mcp_doc_password@localhost:5432/slcn',
)
```
- `.env`에 네이버 API 키·Gemini API 키로 보이는 실값이 평문으로 존재한다(.gitignore 등록은 되어 있음). `STOCKAPP_DATABASE_URL` 미주입 시 앱이 실패하지 않고 하드코딩된 자격증명으로 **조용히 폴백**한다. `jwt_secret`·`gemini_api_key` 등 필수 시크릿에 대한 기동 시점 검증이 어디에도 없다.
- 시나리오: 운영 배포에서 시크릿 마운트가 실패해도 정상 기동한 것처럼 보이다가, 잘못된 DB에 붙거나 전 요청 401로 조용히 무너진다.
- **개선**: 운영 환경에서 필수 시크릿 누락 시 fail-fast(기동 거부). 노출됐을 수 있는 키는 회전 권장.

### 2-5. `rebuild_page_only=true`인데 뉴스를 매번 재수집

- 위치: `app/batch/steps/collect_news.py`
- `DedupeArticlesStep`·`BuildClustersStep`·`CollectMarketIndicesStep`은 모두 `rebuild_page_only` 조기 반환 가드가 있지만 `CollectNewsStep`에만 없다. 재빌드 모드에서도 전체 키워드에 대해 네이버 API를 호출해 `news_article_raw`에 insert하지만, 다음 스텝(중복제거)이 스킵되므로 **수집된 데이터는 그날 다시 사용되지 않는다**. API 쿼터·시간 낭비이자 설계 일관성 결함.
- **개선**: 다른 스텝과 동일한 가드 추가.

---

## 3. 잠재 오류 가능성 — 심각도: 중간

### 3-1. LLM 호출에 타임아웃이 전혀 없음

- 위치: `app/core/llm.py:22-30`
- `ChatGoogleGenerativeAI`에 `max_retries`만 설정하고 `timeout`이 없으며, `invoke_json`의 `ainvoke` 호출부에도 `asyncio.wait_for` 상한이 없다. 네이버 API(`naver_news_timeout_seconds`)·크롤링(`article_crawl_timeout_seconds`)은 타임아웃 설정이 있는데 LLM만 빠져 있다. Gemini 응답 지연 시 배치가 무한정 멈춘다(2-1의 RUNNING 고착과 결합하면 치명적).
- **개선**: `STOCKAPP_LLM_TIMEOUT_SECONDS` 설정 추가.

### 3-2. LLM/크롤링 호출이 전부 순차 — 소요 시간·rate limit 위험

- 위치: `app/batch/steps/build_clusters.py:99-157`, `generate_ai_summaries.py:93-179`, `dedupe_articles.py:81-126`
- 클러스터별 LLM 호출과 기사 크롤링이 모두 for 루프 순차 `await`이다. 클러스터 20개면 카드+상세 포함 40회 이상의 연속 Gemini 호출이 발생해 배치 시간이 선형 증가하고 429(rate limit)에 취약하다. 같은 코드베이스의 `MarketIndexProvider`는 `asyncio.gather`로 병렬 처리한다 — 패턴 불일치.
- **개선**: `asyncio.gather` + `Semaphore`로 동시성 상한 적용.

### 3-3. 배치 동시 트리거 레이스 → 의도된 409 대신 500

- 위치: `app/domains/batches/service.py:66-89`
- 활성 잡 체크(SELECT)와 INSERT가 분리되어 있어, 거의 동시에 두 요청이 오면 두 번째의 INSERT가 유니크 제약 위반(`IntegrityError`)을 일으키고, 이를 `ConflictError`로 변환하는 코드가 없어 500이 반환된다.
- **개선**: INSERT를 `try/except IntegrityError`로 감싸 409 변환.

### 3-4. 클러스터 상세의 `sourceSummary`가 조회되고도 응답에서 유실 (직접 확인)

- 위치: `app/db/repositories/cluster_repo.py:127`(SELECT함), `app/domains/clusters/assembler.py:50-67`(매핑 안 함), `app/schemas/cluster.py:21`(필드 존재)
- 리포지토리는 `source_summary`를 조회하고 스키마·API 문서에도 필드가 있지만, 어셈블러가 매핑하지 않아 **항상 null**로 내려간다. 프론트엔드가 렌더링하려 하면 빈 값만 받는다.
- **개선**: `sourceSummary=article.get('source_summary')` 한 줄 추가.

### 3-5. 타임존/시각 포맷 불일치 — 공용 유틸이 정의만 되고 미사용 (직접 확인)

- 위치: `app/core/timezone.py:15-18`(UTC 정규화 + `Z` 포맷 함수, grep 결과 사용처 없음), `app/domains/pages/assembler.py:20-27`·`clusters/assembler.py:13-20`(각자 `_as_iso` 중복 정의, 타임존 정규화 없이 `isoformat()`만 호출)
- `generatedAt`/`publishedAt` 등이 DB 드라이버 반환값 그대로 내려가 API 문서 예시 포맷과 다를 수 있고, 소비자가 UTC인지 KST인지 API만 보고 확정할 수 없다.
- **개선**: `_as_iso` 제거 후 `isoformat_datetime`으로 통일, 문서 예시 동기화.

### 3-6. 운영 환경에서 CORS 미들웨어 자체가 등록 안 됨 (직접 확인)

- 위치: `app/main.py:13`
```python
if settings.is_development and settings.cors_allowed_origins_list:
```
- `app_env != development`면 origin을 설정해도 CORS가 비활성이다. 운영에서 별도 도메인 프론트엔드가 브라우저에서 직접 호출하면 차단된다. 리버스 프록시 동일 오리진 전제라면 의도일 수 있으나 문서화가 없다.
- **개선**: 화이트리스트 존재 여부만으로 활성화하거나, 의도된 설계라면 주석/문서로 명시.

### 3-7. JWT 시크릿 설정 오류가 전 요청 401로만 노출

- 위치: `app/api/deps/auth.py:112-135`
- 시크릿은 base64url 인코딩 필수인데, 형식이 잘못되거나 비어 있어도 기동은 성공하고 모든 요청이 `AUTH_INVALID_TOKEN` 401로 실패한다. "토큰 문제"와 "설정 문제"를 운영자가 구분할 수 없다.
- **개선**: 기동 시 base64url 디코딩 가능 여부 검증(fail-fast).

### 3-8. 스키마 정의가 3곳에 중복 — 드리프트 위험 + 마이그레이션 도구 부재

- 위치: `app/db/models/*`(앱 어디서도 미사용, `stock` 스키마명 하드코딩), `app/db/repositories/*`(raw SQL, 스키마명은 설정값), `db/schema_postgresql.sql`(수동 실행 DDL)
- ORM 모델은 사용되지 않는데 존재하고, Alembic 등 마이그레이션 도구가 없어 컬럼 변경 시 세 곳의 정합성을 수동으로 맞춰야 한다. 불일치는 배포 후 런타임 `column does not exist`로만 발견된다. 설계 문서(`docs/postgresql_ddl_design.md`)와 실제 DDL도 스키마명(`market` vs `stock`)·타입(`UUID` vs `TEXT`)이 어긋나 있다.
- **개선**: 미사용 ORM 모델 제거 또는 Alembic 전환으로 단일 진실 공급원 확립. 설계 문서 동기화.

### 3-9. 페이지 버전 채번에 설계 문서가 "필수"라고 명시한 advisory lock 미구현

- 위치: `app/db/repositories/page_snapshot_write_repo.py:18-27` vs `docs/postgresql_ddl_design.md:1114-1127`
- 단순 `MAX(version_no)+1` 조회다. 현재는 배치 중복 실행이 DB 제약으로 막혀 실위험이 낮지만, 수동 개입이나 동시성 정책 변경 시 `version_no` 충돌 가능.
- **개선**: `pg_advisory_xact_lock` 적용 또는 유니크 제약 충돌 시 재시도.

### 3-10. `ai_summary.model_name` 하드코딩 — 해결됨

- LLM 클라이언트가 실제 설정 모델명을 노출하고 배치가 이를 저장한다.
- 기본 모델과 `.env.example`은 현재 키로 live 호출이 확인된 `gemini-3.1-flash-lite`로 통일했다. 이 모델은 [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite)에서 Standard Free Tier의 입·출력 토큰을 무료로 제공한다.
- 운영에서 `STOCKAPP_LLM_MODEL`을 재정의하면 호출과 감사 정보에 같은 모델명이 사용된다.
- `gemini-2.5-flash`도 [공식 가격표](https://ai.google.dev/gemini-api/docs/pricing#gemini-2.5-flash)와 현재 프로젝트의 `models.list`에서는 Free Tier·`generateContent` 지원 모델로 표시됐다. 그러나 2026-07-28 최소 live 검증에서 LangChain과 Google Gen AI SDK 직접 호출이 모두 `404 NOT_FOUND`를 반환했다. Google 측 모델 목록과 실행 endpoint의 불일치가 해소되기 전에는 운영 기본값으로 사용하지 않는다.

### 3-11. 헬스체크/레디니스 엔드포인트 부재

- 위치: 라우터 전체, `Dockerfile`(HEALTHCHECK 없음)
- `/health`·`/readyz`가 없어 오케스트레이터가 DB 연결 불능 상태를 감지하지 못한다.
- **개선**: 프로세스 생존용 `/health` + DB 확인용 `/readyz` 추가.

---

## 4. 잠재 오류 가능성 — 심각도: 낮음

| # | 위치 | 내용 |
|---|---|---|
| 4-1 | `app/batch/providers/naver_news.py:182` | `except TypeError, ValueError, IndexError:` — Python 3.14의 PEP 758로 **현재는 유효한 문법**(`requires-python >= 3.14`, `py_compile` 통과 확인). 다만 3.13 이하에서는 SyntaxError이므로 이식성·팀원 로컬 환경·린터 호환 관점에서 괄호 형태 `except (TypeError, ValueError, IndexError):` 권장 |
| 4-2 | `app/batch/steps/collect_news.py:46-68` | 키워드별 실패 격리 없음 — 키워드 1개의 네이버 API 예외로 스텝 전체 중단, 부분 성공 표현 불가 |
| 4-3 | `app/batch/steps/build_page_snapshot.py:58-61`, `generate_ai_summaries.py:58-60` | "클러스터 없음" 실패 분기에서 WARN/ERROR 이벤트를 남기지 않아 `batch_job_event`만 봐서는 원인 추적 불가 (다른 스텝은 WARN 기록) |
| 4-4 | `app/db/session.py:18-24` | 커넥션 풀 미튜닝 (`pool_size`/`max_overflow`/`pool_recycle` 부재) — 관리형 PG의 유휴 커넥션 강제 종료 시 간헐 오류 가능 |
| 4-5 | `app/db/session.py:47-50` | `get_db_session`에 명시적 rollback 경계 없음 — 현재는 읽기 전용이라 무해하나 쓰기 라우터 추가 시 위험 |
| 4-6 | `app/core/request_context.py:23-25` | `X-Request-Id` 헤더를 길이/형식 검증 없이 로그·응답에 반영 (로그 주입 여지) |
| 4-7 | `app/core/settings.py:23` | `auth_stub_token` — 어디서도 참조되지 않는 죽은 설정 |
| 4-8 | `app/batch/steps/build_clusters.py:108` | `ordered_articles[0]` — 값을 사용하지 않는 no-op 죽은 코드 |
| 4-9 | `app/schemas/cluster.py:35` vs `page.py:31` | 동일 개념 `articleCount`가 한쪽은 Optional, 한쪽은 필수 — DB는 NOT NULL이므로 Optional 불필요 |
| 4-10 | `app/db/repositories/page_snapshot_repo.py:262-273` | 아카이브만 SQL 별칭이 camelCase로 스키마 필드명과 암묵 결합 — 필드명 변경 시 런타임에만 발견 |

---

## 5. 실제 사용 시 불편한 점

### 운영자 관점

1. **배치 실패 복구가 전부 수동 DB 작업** (2-1) — 취소/리셋/재시도 API가 없어 고착 시 psql 접속이 유일한 수단.
2. **`rebuild_page_only`가 이름과 달리 비용을 다 씀** — 뉴스 재수집(2-5)에 더해, `BuildPageSnapshotStep`이 현재 job_id의 요약만 조회하는 구조라 `GenerateAiSummariesStep`을 건너뛸 수 없어 **"페이지만 다시 만들기"인데 Gemini 요약 비용이 매번 발생**한다.
3. **관측성 부족** — 요청 ID는 부여되지만 이를 로그 라인에 바인딩하는 로깅 설정이 없고, 배치 로그는 `batch_job_event` 테이블에만 쌓이며 일부 실패 분기는 이벤트조차 안 남긴다(4-3). 배치 실패 시 외부 알림(Slack 등) 없음.
4. **로컬 환경 구성 가이드 부재** — `db/schema_postgresql.sql` 적용 방법, 필요한 `.env` 키 목록이 README에 없다. `.env.example`도 없다.
5. **스키마 변경 절차 부재** (3-8) — 마이그레이션 도구가 없어 환경 간 스키마 버전이 벌어지기 쉽다.

### API 소비자(프론트엔드) 관점

6. **시각 필드의 타임존을 확정할 수 없음** (3-5) — UTC/KST 여부와 포맷이 문서와 불일치할 수 있다.
7. **`sourceSummary`가 항상 null** (3-4) — 문서에는 값이 채워진 예시가 있으나 실제로는 유실.
8. **"최신 버전 여부" 판별 불가** — `DailyPageResponse`에 `isLatest`가 없어 최신 배지를 띄우려면 `/pages/daily/latest`를 추가 호출해 `versionNo`를 비교해야 한다. 문서의 "API 1회 호출로 렌더링" 원칙과 상충.
9. **404 원인 구분 불가** — `businessDate`는 있는데 `versionNo`만 없는 경우와 날짜 자체가 없는 경우가 같은 `PAGE_NOT_FOUND`로 반환되어 "다른 버전 안내" 같은 UX를 만들 수 없다 (`app/domains/pages/service.py:23-35`).
10. **캐싱 헤더 전무** — 과거 날짜의 READY 페이지는 불변인데 `Cache-Control`/`ETag`가 없어 아카이브 재방문마다 전체 페이로드 재전송.
11. **잘못된 `status` 필터 입력 시 의미 없는 500** (2-3) — 422 + 명확한 메시지가 아니라 봉투 깨진 500.
12. **페이지 상세 응답 지연 소지** — 지수/클러스터/기사링크 3개 쿼리가 의존성 없이 순차 실행 (`app/domains/pages/service.py:43-52`). `asyncio.gather`로 단축 가능.

---

## 6. 잘 되어 있는 점

- **SQL Injection 방어**: 전 리포지토리가 파라미터 바인딩 사용, 스키마/테이블명은 `qualify_db_identifier` 정규식 검증(`app/db/identifiers.py`). 악성 스키마명 방어 테스트 존재.
- **JWT 검증이 꼼꼼함**: `exp`/`iss`/`aud`/`sub`/`token_type`/`roles` 전부 필수 강제, refresh 토큰 오용 거부. 실패 케이스 테스트 커버리지 양호(`tests/api/test_auth.py`).
- **배치 트랜잭션 경계**: 스텝별 커밋 + 실패 시 롤백 → 실패 이벤트 기록 → FAILED 마킹 순서가 견고하고 테스트로 검증됨.
- **동시 배치 이중 방어**: 애플리케이션 체크 + DB 부분 유니크 인덱스(단, 3-3의 레이스 응답 코드 문제는 남음).
- **N+1 없음**: 페이지 조립이 `IN` 절 배치 쿼리로 구성됨.
- **부분 실패 처리 모범 사례 존재**: `MarketIndexProvider`의 `gather(return_exceptions=True)` 격리, `ArticleContentProvider`의 폴백 체인, 요약 생성 함수들의 폴백+오류 컨텍스트 기록.
- **비즈니스 날짜(KST) 계산 일관**: `get_business_date`와 네이버 기사 시각의 KST 변환이 날짜 경계와 정확히 맞춰져 있음.
- **응답 봉투 일관성**: `ApiSuccess`/`ApiError` + `meta.requestId`/`timestamp` 자동 부여(정상 경로 한정).

---

## 7. 우선순위 권고 (조치 순서 제안)

| 순위 | 항목 | 근거 |
|---|---|---|
| 1 | RUNNING 고착 복구 수단 (2-1) + LLM 타임아웃 (3-1) | 두 결함이 결합하면 "영원히 안 끝나고 재실행도 못 하는 배치"가 됨. 서비스 핵심 기능(일일 수집)의 가용성 문제 |
| 2 | 필수 시크릿 fail-fast + DB URL 기본값 제거 (2-4) | 운영 사고를 조용한 실패에서 즉시 발견으로 전환. 노출 가능성 있는 키 회전 |
| 3 | `_enrich_cluster` 파싱 보호 (2-2) | 한 줄 수정으로 "배치 전체 실패" 모드 하나 제거 |
| 4 | 일반 예외 핸들러 + `status` enum 검증 (2-3) | API 계약 보장, 프론트 방어 코드 단순화 |
| 5 | `rebuild_page_only` 가드 (2-5) + `sourceSummary` 매핑 (3-4) | 각각 몇 줄 수정으로 비용 낭비·기능 결손 해소 |
| 6 | 헬스체크 (3-11), LLM 병렬화 (3-2), 타임존 통일 (3-5) | 운영 안정성·성능·소비자 경험 개선 |
| 7 | Alembic 도입·ORM 정리 (3-8), 나머지 낮음 항목 | 중장기 유지보수성 |

---

## 8. 최종 조치 결과, 2026-07-14

아래 표는 기존 분석 항목을 보존한 상태에서, 현재 소스와 문서에서 확인한 조치 결과만 덧붙인 것이다. 최종 회귀 검증은 오프라인 테스트 설정으로 `UV_CACHE_DIR=/tmp/uv-cache uv run pytest`를 실행해 **147 passed, 1 skipped, 2 warnings**로 통과했다. 실제 운영 `.env` 값이나 외부 provider 실제 키는 확인하거나 복사하지 않았다.

| 원문 항목 | 최종 상태 | 반영 내용 | 검증 근거 |
|---|---|---|---|
| 2-1 RUNNING 고착 | 완화됨 | 새 배치 시작 전에 6시간 초과 PENDING/RUNNING 잡을 FAILED로 전환하고 커밋한다. 전용 워커 큐와 관리자 강제 리셋 API는 아직 전략 과제다. | `app/domains/batches/service.py`의 `STALE_ACTIVE_JOB_AFTER`, `terminalize_stale_active_jobs`; `tests/domains/test_batches_service.py`, `tests/repositories/test_batch_job_repo.py` |
| 2-2 LLM 응답 파싱 예외 | 구현됨 | 클러스터 enrich 결과와 AI summary 응답이 유효한 dict인지 확인하고, `tags`, `analysis_paragraphs`, 대표 기사 인덱스 등 malformed 값은 폴백과 WARN/오류 메타데이터로 처리한다. | `app/batch/steps/build_clusters.py`, `app/batch/steps/generate_ai_summaries.py`; `tests/batch/test_build_clusters_step.py`; 최종 `UV_CACHE_DIR=/tmp/uv-cache uv run pytest` 통과(147 passed, 1 skipped, 2 warnings) |
| 2-3 공통 예외 핸들러 부재 | 구현됨 | `AppError`, 요청 검증 오류, 최후 `Exception` 핸들러가 모두 `ApiError` 봉투를 반환한다. 아카이브 `status`도 `Literal['READY','PARTIAL','FAILED']`로 제한한다. | `app/core/exceptions.py`, `app/domains/archive/router.py`; `tests/api/test_pages.py` |
| 2-4 시크릿과 기본 DB 설정 | 완화됨 | strict production 설정 검증은 유지하면서, 오프라인 테스트는 명시적 테스트 설정으로 실행되도록 정리했다. Naver와 Gemini 키의 운영 fail-fast 범위는 별도 정책 판단이 필요하다. | `app/core/settings.py`; `tests/core/test_settings.py`; 최종 `UV_CACHE_DIR=/tmp/uv-cache uv run pytest` 통과(147 passed, 1 skipped, 2 warnings) |
| 2-5 `rebuild_page_only` 뉴스 재수집 | 구현됨 | 뉴스 수집, 중복 제거, 클러스터 생성, AI 요약 단계가 `rebuild_page_only=true`에서 provider 호출을 건너뛴다. | `app/batch/steps/collect_news.py`, `dedupe_articles.py`, `build_clusters.py`, `generate_ai_summaries.py`; `tests/batch/test_remaining_batch_step_contracts.py` |
| 3-1 LLM 타임아웃 없음 | 구현됨 | `asyncio.wait_for`와 `STOCKAPP_LLM_TIMEOUT_SECONDS`로 LLM 호출 상한을 둔다. timeout은 `LlmTimeoutError`로 표준화된다. | `app/core/llm.py`, `app/core/settings.py`; `tests/core/test_llm.py` |
| 3-2 LLM/크롤링 순차 처리 | 완화됨 | 클러스터 enrich, AI summary 생성, 기사 본문 fetch에 semaphore 기반 제한 동시성을 적용했다. 외부 rate limit 운영값 튜닝은 남아 있다. | `app/batch/steps/build_clusters.py`, `generate_ai_summaries.py`, `dedupe_articles.py`; `tests/core/test_settings.py`, batch step tests |
| 3-3 동시 트리거 레이스 | 구현됨 | `create_job` 중 `IntegrityError`를 rollback 후 `BATCH_ALREADY_RUNNING` 409로 변환한다. | `app/db/repositories/batch_job_repo.py`, `app/domains/batches/service.py`; `tests/domains/test_batches_service.py`, `tests/repositories/test_batch_job_repo.py` |
| 3-4 `sourceSummary` 유실 | 구현됨 | 대표 기사와 기사 목록 응답 모두 `sourceSummary`를 매핑한다. | `app/domains/clusters/assembler.py`; `tests/api/test_clusters.py`, `tests/domains/test_cluster_assembler.py`, `tests/domains/test_clusters_service.py` |
| 3-5 타임존/시각 포맷 불일치 | 완화됨 | 페이지와 클러스터 assembler, page schema가 `isoformat_datetime`을 통해 timestamp를 정규화한다. 전체 API 문서 예시 동기화는 계속 관리가 필요하다. | `app/domains/pages/assembler.py`, `app/domains/clusters/assembler.py`, `app/schemas/page.py` |
| 3-6 운영 CORS | 결정 유보 | 코드상 CORS는 여전히 development 환경에서만 등록된다. 운영 브라우저 호출을 허용할지, 동일 origin 또는 proxy 전제를 유지할지는 배포 정책 결정 없이는 안전하게 바꿀 수 없다. | `app/main.py`; README의 production CORS 정책 메모 |
| 3-7 JWT 설정 오류 | 구현됨 | production 시작 검증에서 JWT secret 누락, base64url 디코딩 실패, 32바이트 미만 secret을 거부한다. | `app/core/settings.py`; `tests/core/test_settings.py` |
| 3-8 스키마 중복과 migration 부재 | 결정 유보 | `db/schema_postgresql.sql`을 현재 source of truth로 문서화했다. ORM 제거, Alembic 도입, 배포 migration 승인 절차는 schema governance 결정 없이는 문서만으로 변경하지 않는다. | README, `db/schema_postgresql.sql` |
| 3-9 advisory lock 미구현 | 구현됨 | 페이지 버전 채번 전에 business date 기반 `pg_advisory_xact_lock`을 획득한다. | `app/db/repositories/page_snapshot_write_repo.py`; `tests/repositories/test_page_snapshot_repo.py` |
| 3-10 `ai_summary.model_name` 하드코딩 | 구현됨 | LLM client의 설정된 model name을 provider가 노출하고, summary 저장 payload에 반영한다. | `app/core/llm.py`, `app/batch/providers/llm_provider.py`, `app/batch/steps/generate_ai_summaries.py`; `tests/core/test_llm.py` |
| 3-11 health/readiness 부재 | 구현됨 | `/stock/api/health`가 인증 없이 DB `SELECT 1`을 검사하고 장애 시 503 에러 봉투를 제공한다. | `app/api/health.py`, `app/api/router.py`; `tests/api/test_health.py` |
| 4-1 Python 3.14 전용 except 문법 | 남은 전략 과제 | 프로젝트는 `requires-python >=3.14`라 현재 문법은 목표 런타임과 일치한다. 3.13 이하 호환을 지원할지는 별도 portability 결정이다. | `pyproject.toml`; 원문 코드 기준 |
| 4-2 키워드별 provider 실패 격리 | 구현됨 | Naver 키워드별 수집 실패를 WARN 이벤트와 warning message로 기록하고 다음 키워드를 계속 처리한다. | `app/batch/steps/collect_news.py` |
| 4-3 실패 분기 이벤트 부족 | 완화됨 | 클러스터 없음, summary 생성 스킵, provider fallback, malformed LLM summary, snapshot 조립 방어 등 주요 실패 분기가 폴백·WARN 이벤트·오류 메타데이터로 추적된다. 모든 외부 알림 채널까지 연결되지는 않았다. | `app/batch/steps/build_clusters.py`, `generate_ai_summaries.py`, `dedupe_articles.py`, snapshot 관련 방어 테스트; 최종 `UV_CACHE_DIR=/tmp/uv-cache uv run pytest` 통과(147 passed, 1 skipped, 2 warnings) |
| 4-4 DB 커넥션 풀 미튜닝 | 구현됨 | `pool_pre_ping`, `pool_size`, `max_overflow`, `pool_timeout`을 설정에서 조정할 수 있다. | `app/db/session.py`, `app/core/settings.py`; `tests/core/test_db_identifiers.py` |
| 4-5 DB session rollback 경계 | 구현됨 | `get_db_session` dependency가 예외 발생 시 rollback 후 재전파한다. | `app/db/session.py` |
| 4-6 request ID 검증 | 구현됨 | `X-Request-Id`는 안전한 문자와 128자 길이만 반영하고, 나머지는 generated request ID로 교체한다. | `app/core/request_context.py`; `tests/api/test_health.py` |
| 4-7 `auth_stub_token` 죽은 설정 | 남은 전략 과제 | 설정은 아직 남아 있다. 제거는 호출자 호환성 확인 뒤 별도 정리로 처리하는 것이 안전하다. | `app/core/settings.py` |
| 4-8 no-op 코드 | 남은 전략 과제 | 현재 문서 작업 범위에서 애플리케이션 코드는 더 변경하지 않았다. 동작 영향이 낮아 후속 정리 대상으로 둔다. | 원문 항목 기준 |
| 4-9 `articleCount` Optional 불일치 | 구현됨 | 페이지 카드와 클러스터 상세 응답 모두 `articleCount`를 필수 int로 선언한다. | `app/schemas/page.py`, `app/schemas/cluster.py` |
| 4-10 아카이브 camelCase 별칭 결합 | 남은 전략 과제 | 런타임 결함은 확인되지 않았고, 리포지토리 계약 개선은 별도 refactor 범위로 남긴다. | 원문 항목 기준 |

### 배포 정책상 유보된 결정

| 결정 항목 | 현재 결론 | 이유 |
|---|---|---|
| Production CORS | 결정 유보 | 운영 프론트엔드가 별도 origin에서 직접 API를 호출하는지, 동일 origin reverse proxy를 쓰는지 확인 없이 CORS 정책을 넓히면 보안 경계가 바뀐다. |
| Schema와 migration governance | 결정 유보 | 현재 source of truth는 `db/schema_postgresql.sql`이다. Alembic 도입, ORM 제거, DDL 승인 절차는 팀 운영 방식과 배포 권한을 먼저 정해야 한다. |
| 외부 실패 알림 | 결정 유보 | Slack, paging, email 등 알림 채널, 수신자, 심각도 기준, 개인정보와 시크릿 redaction 규칙이 필요하다. |

### 문서 업데이트 결과

- strict production 설정을 유지하면서 오프라인 테스트에 필요한 설정을 명시적으로 분리했다.
- malformed LLM summary와 snapshot 조립 방어까지 포함한 최종 회귀 검증으로 `UV_CACHE_DIR=/tmp/uv-cache uv run pytest`를 실행했고, 결과는 **147 passed, 1 skipped, 2 warnings**였다.
- 이 결과는 오프라인 테스트 기준이다. live integration, 실제 provider 호출, 운영 DB 검증을 완료했다는 의미는 아니다.
