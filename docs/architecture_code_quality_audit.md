# 아키텍처 및 코드 품질 분석 보고서

작성일: 2026-05-14

## 요약

현재 코드는 FastAPI 애플리케이션 계층, 도메인 서비스, SQLAlchemy 기반 저장소, 배치 오케스트레이션, 외부 API 연동이 비교적 명확한 디렉터리 구조로 나뉘어 있습니다. `app/api`, `app/domains`, `app/db`, `app/batch`, `app/core`의 큰 틀은 유지보수 가능한 방향입니다.

다만 구현 세부에서는 계층 경계가 일부 역전되어 있고, 배치 트랜잭션 단위가 저장소/스텝 곳곳에 흩어져 있으며, 스키마 이름을 SQL 문자열에 직접 삽입하는 패턴이 반복됩니다. 또한 외부 API 실패가 조용히 fallback 처리되는 지점이 있어 운영 중 데이터 품질 문제를 파악하기 어렵습니다.

우선순위가 높은 개선 대상은 다음입니다.

1. 도메인 서비스가 FastAPI 의존성 및 API 응답 스키마에 결합되어 있음
2. DB schema/table identifier를 raw string으로 SQL에 삽입함
3. 배치 작업의 commit/rollback 경계가 오케스트레이터가 아닌 저장소와 스텝에 분산됨
4. 외부 API 실패가 로그/이벤트 없이 숨겨지는 지점이 있음
5. 실패 경로, 부분 실패, rollback, provider parsing 테스트가 부족함

검증 참고: `app` 디렉터리 LSP diagnostics 결과는 오류 0건이었습니다. 즉, 아래 내용은 문법/타입 오류보다는 구조적 유지보수성, 운영 안정성, 테스트 가능성 관점의 개선 제안입니다.

## 현재 구조 평가

### 장점

- `app/main.py`와 `app/api/router.py`를 통해 애플리케이션 생성과 라우터 조립이 분리되어 있습니다.
- 기능별 도메인이 `app/domains/pages`, `app/domains/archive`, `app/domains/clusters`, `app/domains/batches`로 나뉘어 있습니다.
- DB projection DTO가 `app/db/repositories/projections.py`에 모여 있어 raw query 결과를 무작정 dict로만 다루지 않으려는 방향이 보입니다.
- 테스트 디렉터리가 `tests/api`, `tests/domains`, `tests/repositories`, `tests/batch`로 나뉘어 있어 개선을 이어가기 좋은 기반이 있습니다.
- `pyproject.toml`에 Ruff, vulture 설정이 존재합니다.

### 주요 리스크

- 서비스 계층이 API 계층의 `DbSession` alias와 Pydantic response schema를 직접 사용합니다.
- 여러 저장소가 `get_settings().database_schema`를 table/enum 이름에 직접 보간합니다.
- batch job 생성, event 기록, step 처리 중간중간에 commit이 발생해 하나의 batch run을 원자적으로 이해하기 어렵습니다.
- 일부 provider와 LLM fallback 로직이 broad exception을 삼켜 원인 추적성이 낮습니다.
- 테스트는 happy path와 repository SQL 형태는 어느 정도 확인하지만, 운영 장애로 이어질 수 있는 부분 실패와 fallback 경로를 충분히 보호하지 않습니다.

## 상세 분석

### 1. 도메인 서비스가 API 계층에 결합됨

심각도: 높음

근거:

- `app/domains/pages/service.py:5`에서 `app.api.deps.DbSession`을 import합니다.
- `app/domains/pages/service.py:9`, `app/domains/pages/service.py:16`, `app/domains/pages/service.py:28`에서 서비스 반환 타입이 `DailyPageResponse`입니다.
- `app/domains/batches/service.py:5`에서 `BackgroundTasks`를, `app/domains/batches/service.py:7`에서 `DbSession`을 import합니다.
- `app/domains/batches/service.py:14-21`에서 batch 서비스가 API 응답 schema를 직접 조립합니다.

문제점:

도메인 서비스가 API 의존성 alias와 응답 schema에 묶이면 HTTP 외부에서 재사용하기 어렵습니다. 예를 들어 CLI, batch, worker, 테스트에서 서비스를 사용하려고 해도 FastAPI dependency 형태와 API response model을 함께 끌고 들어오게 됩니다. 또한 도메인 로직 변경과 API 응답 계약 변경이 같은 파일에 섞입니다.

권장 수정:

- `DbSession` 같은 FastAPI dependency alias는 `app.api.deps`에만 두고, 도메인 service factory는 `AsyncSession`을 직접 받거나 `app/core/dependencies.py`처럼 HTTP와 무관한 위치로 옮깁니다.
- 서비스는 repository record 또는 domain DTO를 반환하고, `app.schemas.*` 응답 모델 조립은 router 또는 assembler에서 수행합니다.
- `BatchJobScheduler`처럼 FastAPI `BackgroundTasks`에 직접 의존하는 객체는 API layer adapter로 분리합니다.

권장 테스트:

- `tests/domains/*_service.py`에서 FastAPI import 없이 service를 생성할 수 있는지 확인합니다.
- router 테스트는 response schema 조립만 검증하고, service 테스트는 domain DTO 반환과 예외만 검증합니다.

### 2. 라우터 prefix 소유권이 분산되어 URL과 모듈 경계가 어긋남

심각도: 중간

근거:

- `app/api/router.py:8`에서 공통 prefix `/stock/api`를 선언합니다.
- `app/api/router.py:10`에서 `archive_router`를 `/pages` 아래에 mount합니다.
- `app/domains/archive/router.py`는 자체적으로 `/archive` path를 갖기 때문에 최종 경로가 `/stock/api/pages/archive`가 됩니다.
- 각 도메인 router는 `APIRouter()`만 선언하고 prefix/tags는 aggregator에서 부여합니다.

문제점:

archive 기능이 `domains/archive`에 있지만 API URL은 pages 하위 리소스처럼 보입니다. 기능이 늘어날수록 라우터 소유권을 파일 하나에서만 추적하기 어려워지고, 라우트 이동 시 API 경로 변경 위험이 커집니다.

권장 수정:

- 각 도메인 router가 자신의 prefix와 tag를 소유하도록 변경합니다.
- 예: `pages.router = APIRouter(prefix='/pages', tags=['pages'])`, `archive.router = APIRouter(prefix='/archive', tags=['archive'])`
- `app/api/router.py`는 `api_router.include_router(pages_router)`처럼 조립만 담당합니다.

권장 테스트:

- `tests/api`에 route registration contract 테스트를 추가해 주요 endpoint path가 의도한 값인지 확인합니다.

### 3. SQL identifier에 schema 이름을 raw string으로 보간함

심각도: 높음

근거:

- `app/db/session.py:29`에서 `SET search_path TO {settings.database_schema}, public`을 f-string으로 실행합니다.
- `app/db/repositories/page_snapshot_repo.py:11-12`에서 `return f'{get_settings().database_schema}.{table_name}'`를 사용합니다.
- `app/db/repositories/page_snapshot_write_repo.py:13-14`도 동일한 `_qualified_table()` 패턴을 사용합니다.
- 같은 패턴이 `cluster_repo`, `batch_job_repo`, `news_article_raw_repo`, `news_search_keyword_repo`, `news_cluster_write_repo`, `market_index_repo`, `ai_summary_write_repo`에도 반복됩니다.

문제점:

bind parameter는 값에는 안전하지만 table/schema/enum identifier에는 사용할 수 없습니다. 그래서 identifier 보간 자체는 필요할 수 있으나, 현재처럼 환경 변수 값을 그대로 넣으면 잘못된 schema 값으로 SQL이 깨지거나 의도하지 않은 SQL identifier가 만들어질 수 있습니다. 특히 `search_path`는 connection 생성 시 실행되므로 설정 오류가 애플리케이션 전체 장애로 이어집니다.

권장 수정:

- `database_schema`는 시작 시점에 정규식으로 엄격히 검증합니다. 예: `^[A-Za-z_][A-Za-z0-9_]*$`
- schema/table/enum 이름 생성은 공통 helper 한 곳으로 통합합니다.
- 가능하면 SQLAlchemy `quoted_name`, dialect identifier preparer, 또는 psycopg SQL identifier quoting을 사용합니다.
- raw SQL을 유지하더라도 `_qualified_table()`이 검증된 schema와 allowlisted table name만 반환하도록 제한합니다.

권장 테스트:

- `tests/core/test_settings.py` 또는 `tests/db/test_sql_identifier.py`를 추가해 유효하지 않은 schema 이름이 설정 단계에서 거부되는지 확인합니다.
- repository SQL 생성 테스트에서 허용 table name 외 입력이 불가능한 구조인지 확인합니다.

### 4. 배치 트랜잭션 경계가 저장소와 스텝에 분산됨

심각도: 높음

근거:

- `app/db/repositories/batch_job_repo.py:141`에서 `create_job()`이 commit합니다.
- `app/db/repositories/batch_job_repo.py:185`에서 `add_event()`가 commit합니다.
- `app/db/repositories/batch_job_repo.py:244`에서 `mark_job_completed()`가 commit합니다.
- `app/batch/steps/build_clusters.py:123`, `app/batch/steps/generate_ai_summaries.py:161` 등 step 내부에서도 commit합니다.
- AST 검색 결과 `await ...commit()` 패턴이 app 내부에서 11곳 발견되었습니다.

문제점:

하나의 batch run은 여러 step으로 구성되지만, commit이 repository와 step에 흩어져 있어 실패 시 어떤 데이터가 확정되었는지 예측하기 어렵습니다. 예를 들어 cluster 생성 중 일부 market만 commit된 뒤 다음 step에서 실패하면 batch job은 FAILED가 되지만, 중간 산출물은 남을 수 있습니다. 반대로 event 기록이 매번 commit되기 때문에 rollback이 필요한 도메인 데이터와 운영 로그 데이터의 경계도 명확하지 않습니다.

권장 수정:

- repository는 execute/flush까지만 담당하고 commit/rollback은 orchestrator 또는 service layer가 소유합니다.
- 배치 실행 정책을 명확히 정합니다.
  - 전체 batch 단위 원자성: 하나의 `async with session.begin()`으로 묶기
  - step 단위 확정: 각 step을 명시적 transaction으로 감싸고, 실패 시 보상/cleanup 정책 문서화
  - event log 별도 확정: event 기록용 transaction을 의도적으로 분리하되 코드에 정책을 드러내기
- `BatchJobRepository`의 `create_job`, `add_event`, `mark_job_completed`에서 commit을 제거하고 호출자가 transaction을 제어하도록 변경합니다.

권장 테스트:

- batch step 실패 시 이전 step 산출물이 rollback 또는 정책대로 유지되는지 검증하는 테스트를 추가합니다.
- `tests/batch`에 partial failure 시 job status, event, 생성 데이터의 최종 상태를 확인하는 테스트를 추가합니다.

### 5. 외부 API 실패가 숨겨짐

심각도: 중간-높음

근거:

- `app/batch/providers/market_index_provider.py:55-58`에서 `asyncio.gather(..., return_exceptions=True)` 후 `MarketIndexFetchResult`가 아닌 결과를 버립니다.
- `app/batch/providers/article_content.py:49-64`에서 모든 예외를 잡고 다음 URL로 넘어가며, 최종 fallback에도 실패 이유가 남지 않습니다.
- `app/batch/steps/build_clusters.py:206-210`에서 LLM enrich 실패를 broad `except Exception`으로 fallback 처리합니다.

문제점:

외부 API와 크롤링은 장애가 잦은 영역이므로 fallback 자체는 필요합니다. 하지만 실패 원인이 event/log/context에 남지 않으면 운영자가 데이터 누락, timeout, 인증 오류, HTML 구조 변경을 구분할 수 없습니다. 결과적으로 batch가 성공처럼 보이지만 페이지 품질은 낮아질 수 있습니다.

권장 수정:

- fallback을 유지하되 실패 URL, provider, exception class, message를 batch event 또는 structured log에 남깁니다.
- `MarketIndexProvider`는 ticker별 성공/실패 결과를 구분하는 result type을 반환하도록 변경합니다.
- broad `except Exception`은 외부 boundary에서만 사용하고, 내부 처리에서는 구체적 예외를 우선 처리합니다.

권장 테스트:

- `market_index_provider`에서 ticker 일부 실패 시 실패 정보가 보존되는지 확인합니다.
- `article_content`에서 origin 실패 후 naver_link 성공, 둘 다 실패, HTML parsing 실패 케이스를 mocked `httpx`로 검증합니다.
- LLM provider 실패 시 fallback_used와 error_message가 저장되는지 검증합니다.

### 6. 실행 모드 판단에 `hasattr(session, 'bind')`를 사용함

심각도: 중간

근거:

- `app/batch/steps/build_clusters.py:67`에서 session에 `bind`가 없으면 clustering 방식을 바꿉니다.
- `app/batch/steps/generate_ai_summaries.py:25-27`에서 session에 `bind`가 없으면 AI summary step을 scaffold로 처리합니다.
- 유사 패턴이 `collect_market_indices.py`, `dedupe_articles.py`, `build_page_snapshot.py`에도 존재합니다.

문제점:

테스트 fake session과 실제 SQLAlchemy session을 구분하려는 의도로 보이지만, production 코드가 SQLAlchemy 내부 속성 존재 여부에 의존하게 됩니다. session wrapper, proxy, 테스트 fixture 변경에 따라 실제 작업이 scaffold/no-op으로 전환될 수 있습니다.

권장 수정:

- 실행 모드는 명시적 설정이나 dependency injection으로 표현합니다.
- fake session 분기 대신 repository protocol/fake repository를 주입합니다.
- batch step은 session의 내부 속성보다 필요한 repository method가 있는지 또는 명시적 `BatchExecutionMode`를 기준으로 동작하게 합니다.

권장 테스트:

- 실제 repository를 주입한 step 테스트와 fake repository를 주입한 unit test를 분리합니다.
- scaffold 경로는 테스트 전용으로만 유지하거나 명시적 플래그가 있을 때만 동작하도록 확인합니다.

### 7. API/service 계약에서 중복 validation과 죽은 분기가 존재함

심각도: 중간

근거:

- `app/domains/pages/service.py:16-22`, `app/domains/pages/service.py:45-49`에서 service가 not found를 직접 raise합니다.
- 그런데 `app/domains/pages/router.py:23-28`, `app/domains/pages/router.py:38-43`, `app/domains/pages/router.py:52-55`에서 `payload is None`을 다시 검사합니다.
- `app/domains/batches/router.py:42-49`에서 service가 반환한 `BatchRunResponse`를 다시 `BatchRunResponse.model_validate()`로 감쌉니다.

문제점:

service가 예외를 책임지는지, router가 None을 HTTP 오류로 변환하는지 계약이 불명확합니다. 중복 validation은 작은 비용이지만 계층 간 반환 타입을 오해하게 만들고, 죽은 분기는 향후 수정 시 혼란을 만듭니다.

권장 수정:

- not found 정책을 하나로 정합니다. 현재 구조를 유지한다면 service가 `NotFoundError`를 raise하고 router의 `payload is None` 분기를 제거합니다.
- service가 이미 response model을 반환한다면 router의 `model_validate()`를 제거합니다. 더 나은 방향은 service가 domain DTO를 반환하고 router/assembler에서 한 번만 response model을 만드는 것입니다.

권장 테스트:

- service not found 테스트는 `NotFoundError` 발생만 검증합니다.
- router 테스트는 exception handler를 통해 API error envelope이 나오는지만 검증합니다.

### 8. JWT secret 형식 계약이 암묵적임

심각도: 중간

근거:

- `app/api/deps/auth.py:112-123`에서 `settings.jwt_secret` 문자열을 항상 base64url decode합니다.
- `app/api/deps/auth.py:126-135`는 base64url decode 실패를 invalid token으로 변환합니다.
- 설정 이름은 `jwt_secret`이라 raw secret인지 base64url secret인지 코드만으로 분명하지 않습니다.

문제점:

운영자가 일반 문자열 secret을 설정하면 토큰 검증이 모두 실패할 수 있습니다. 이 경우 실제 원인은 설정 형식 오류인데 사용자에게는 invalid token으로만 보입니다.

권장 수정:

- 환경 변수 이름 또는 문서에 base64url 형식을 명확히 적습니다. 예: `JWT_SECRET_BASE64URL`
- raw secret을 허용할 계획이면 decoding 여부를 설정으로 분리합니다.
- 설정 검증 단계에서 잘못된 secret 형식을 더 명확한 configuration error로 실패시키는 것이 좋습니다.

권장 테스트:

- base64url secret, 빈 secret, 잘못된 base64url secret, raw secret 입력 시 기대 동작을 `tests/api/test_auth.py` 또는 `tests/core/test_settings.py`에 명시합니다.

### 9. batch 성능 병목 가능성

심각도: 중간

근거:

- `app/batch/steps/generate_ai_summaries.py:73-159`에서 market, cluster, card/detail summary 생성을 순차적으로 수행합니다.
- `app/db/repositories/news_article_raw_repo.py:125` 등 write path에서 row-by-row insert 후 commit하는 패턴이 있습니다.
- `app/batch/providers/article_content.py:50`에서 URL마다 새 `httpx.AsyncClient`를 생성합니다.

문제점:

뉴스 수, 클러스터 수, LLM 호출 수가 증가하면 batch latency가 선형 또는 그 이상으로 증가할 수 있습니다. 외부 API rate limit을 고려한 제한된 concurrency가 필요하지만, 현재는 대부분 순차 처리와 반복 client 생성에 가깝습니다.

권장 수정:

- DB insert는 가능한 범위에서 bulk insert 또는 executemany 형태로 묶습니다.
- 외부 API/LLM 호출은 semaphore 기반 제한 concurrency를 적용합니다.
- HTTP client는 provider lifetime 동안 재사용하거나 step 단위로 생성해 넘깁니다.
- 성능 최적화 전에는 batch별 article/cluster/LLM call count, 소요 시간, fallback count를 metric으로 남깁니다.

권장 테스트:

- provider/client factory를 주입 가능하게 만들어 client 생성 횟수와 concurrency 제한을 unit test로 확인합니다.
- batch step benchmark 또는 lightweight performance regression 테스트를 별도 marker로 분리합니다.

### 10. 위험 영역에 대한 테스트 커버리지가 부족함

심각도: 중간-높음

근거:

- `tests/batch/test_remaining_batch_steps.py`, `tests/batch/test_remaining_batch_step_contracts.py`는 scaffold/happy path 성격이 강합니다.
- provider 단위 테스트가 `naver_news.py`, `article_content.py`, `market_index_provider.py`, `llm_provider.py`의 실패/파싱 경로를 충분히 보호하지 않습니다.
- `tests/integration/test_collect_news_live.py`는 live integration 성격이라 credentials/network 상태에 의존합니다.

문제점:

배치와 외부 연동은 장애 비용이 큰 영역인데, 현재 테스트가 rollback, idempotency, partial failure, provider parsing regression을 충분히 잡지 못합니다. live integration은 유용하지만 deterministic unit test를 대체할 수 없습니다.

권장 수정:

- provider별 mocked unit test를 추가합니다.
- batch orchestrator 실패 시 job status/event/data state를 검증합니다.
- 동일 business_date 재실행, force run, rebuild_page_only 케이스를 테스트합니다.
- repository commit 제거 작업 전후에 transaction behavior 테스트를 먼저 작성합니다.

## 권장 수정 순서

### 1단계: 안전성 우선

1. schema identifier 검증/quoting helper 추가
2. `_qualified_table()` 중복 제거 및 공통 helper 적용
3. JWT secret 형식 문서화 또는 설정 검증 추가
4. 외부 API 실패 정보를 event/log에 남기도록 개선

### 2단계: 배치 원자성 정리

1. batch transaction 정책 결정
2. repository 내부 commit 제거
3. orchestrator 또는 step 단위 transaction으로 commit 책임 이동
4. partial failure/rollback 테스트 추가

### 3단계: 계층 경계 정리

1. service factory에서 `app.api.deps.DbSession` 의존 제거
2. service 반환 타입을 API schema에서 domain DTO/projection으로 변경
3. router/assembler에서 API response model을 단일 지점에서 생성
4. route prefix를 feature router가 소유하도록 정리

### 4단계: 테스트와 성능 개선

1. provider parsing/fallback unit test 추가
2. LLM fallback/error_message 저장 테스트 추가
3. batch step idempotency 테스트 추가
4. insert bulk화와 제한 concurrency 적용

## 우선순위 표

| 우선순위 | 항목 | 영향 | 권장 작업 |
| --- | --- | --- | --- |
| P0 | SQL identifier raw 보간 | 설정 오류/SQL injection footgun | schema 검증 및 quoting helper |
| P0 | batch commit 분산 | partial write, rollback 불명확 | transaction 경계 재설계 |
| P1 | domain-service/API 결합 | 재사용성/테스트성 저하 | service DTO와 API schema 분리 |
| P1 | 외부 실패 은닉 | 데이터 품질 문제 추적 어려움 | provider 실패 이벤트/로그 추가 |
| P1 | 실패 경로 테스트 부족 | 회귀 탐지 어려움 | rollback/fallback/provider 테스트 추가 |
| P2 | route prefix 분산 | API 소유권 혼란 | router prefix feature 소유로 변경 |
| P2 | 순차 처리/row-by-row write | batch latency 증가 | bulk insert, 제한 concurrency |
| P2 | JWT secret 계약 불명확 | 운영 설정 오류 | 설정명/문서/검증 정리 |

## 검증 체크리스트

수정 작업을 시작한다면 각 PR에서 아래 항목을 확인하는 것이 좋습니다.

- `uv run pytest tests/api tests/domains tests/repositories tests/batch`
- schema helper 추가 후 invalid schema name 테스트 통과
- batch 실패 시 job status와 event 기록이 기대대로 남는지 확인
- provider failure/fallback 테스트가 deterministic하게 통과
- `uv run ruff check .`
- 필요 시 `uv run vulture`로 dead code 확인

## 결론

현재 구조는 디렉터리 레벨에서는 좋은 출발점이지만, 실제 dependency 방향과 transaction ownership이 섞여 있어 장기 유지보수 시 문제가 커질 수 있습니다. 특히 배치 시스템은 데이터 생성 파이프라인의 핵심이므로 commit 정책과 실패 관측성을 먼저 정리하는 것이 가장 효과적입니다.

가장 작은 단위의 첫 개선은 `database_schema` 검증과 `_qualified_table()` 공통화입니다. 그 다음 repository commit 제거와 batch rollback 테스트를 묶어 진행하면, 이후 service/API 분리와 성능 개선을 더 안전하게 진행할 수 있습니다.
