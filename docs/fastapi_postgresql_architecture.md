# FastAPI PostgreSQL 아키텍처 설계

## 1. 문서 목적

본 문서는 현재 프로젝트의 FastAPI 백엔드 구조를 PostgreSQL 기준으로 설계한다.

범위는 다음을 포함한다.

- API 서버의 폴더 구조
- PostgreSQL DDL과 대응되는 애플리케이션 계층 구조
- 배치 파이프라인과 조회 API의 책임 분리
- 구현 우선순위와 초기 개발 단위 제안

본 문서는 다음 문서를 전제로 한다.

- `docs/Product_Requirement_Document.md`
- `docs/api_spec_doc.md`
- `docs/postgresql_ddl_design.md`
- `db/schema_postgresql.sql`

MariaDB 설계는 본 문서 범위에서 제외한다.

---

## 2. 설계 전제

### 2-1. 제품 전제

현재 제품은 아래 5개 화면을 중심으로 정의되어 있다.

- 최신 시장 페이지
- 날짜별 시장 페이지
- 아카이브 검색 페이지
- 뉴스 클러스터 상세 페이지
- 배치 상태 페이지

핵심 요구는 다음과 같다.

- 미국/한국 시장을 하나의 통합 페이지 단위로 조회해야 한다.
- 페이지와 클러스터 상세는 Frontend가 추가 조합 없이 1회 호출로 렌더링 가능해야 한다.
- 과거 날짜와 버전 재현이 가능해야 한다.
- 운영자는 배치 상태와 실패 원인을 빠르게 확인할 수 있어야 한다.

### 2-2. 데이터 전제

PostgreSQL 설계는 데이터 계층을 아래 두 층으로 나눈다.

- 원천 계층
  - `batch_job`
  - `batch_job_event`
  - `news_article_raw`
  - `news_article_processed`
  - `news_cluster`
  - `news_cluster_article`
  - `market_index_daily`
  - `ai_summary`
- 스냅샷 계층
  - `market_daily_page`
  - `market_daily_page_market`
  - `market_daily_page_market_index`
  - `market_daily_page_market_cluster`
  - `market_daily_page_article_link`

애플리케이션 구조도 이 경계를 그대로 따라야 한다.

### 2-3. PostgreSQL 전용 전제

- `business_date`는 KST 기준 날짜다.
- `TIMESTAMPTZ`를 기본 시각 타입으로 사용한다.
- 상태값은 PostgreSQL enum과 Python enum을 일치시킨다.
- 클러스터 외부 식별자는 `cluster_uid`를 사용한다.
- 부분 유니크 인덱스와 FK 제약을 애플리케이션 로직이 존중해야 한다.

---

## 3. 아키텍처 원칙

### 3-1. 조회와 생성 분리

이 프로젝트는 단순 CRUD 서비스가 아니다.

- 조회 API는 화면 렌더링 최적화가 목적이다.
- 배치 파이프라인은 데이터 수집, 정제, 요약, 스냅샷 생성이 목적이다.

따라서 조회 API와 배치 로직을 같은 서비스 계층에 혼합하지 않는다.

### 3-2. 스냅샷 중심 조회

페이지 API는 원천 테이블을 매 요청마다 조립하지 않는다.

- 최신 페이지 조회
- 날짜별 페이지 조회
- 아카이브 조회

위 API들은 모두 스냅샷 계층 중심으로 구현한다.

이 방식의 장점은 다음과 같다.

- 과거 버전 재현이 쉽다.
- 쿼리 복잡도가 낮다.
- 응답 형태를 안정적으로 유지할 수 있다.
- Frontend 요구사항인 1회 호출 렌더링에 적합하다.

### 3-3. 도메인 단위 API 구조

라우터와 서비스는 테이블 기준이 아니라 외부 API 도메인 기준으로 나눈다.

- `pages`
- `archive`
- `clusters`
- `batches`
- `admin`

이유는 API 명세가 이미 화면 단위로 명확하게 고정되어 있기 때문이다.

### 3-4. 배치 단계 분리

배치는 아래 단계로 분리한다.

1. 배치 생성
2. 뉴스 수집
3. 기사 정제 및 중복 제거
4. 클러스터 생성
5. 지수 수집
6. AI 요약 생성
7. 페이지 스냅샷 생성
8. 배치 상태 확정

각 단계는 독립 테스트가 가능해야 하고, 이벤트 로그를 남길 수 있어야 한다.

### 3-5. 응답 조립 계층 분리

페이지 응답과 클러스터 상세 응답은 ORM 엔터티를 그대로 반환하면 안 된다.

별도 `assembler` 계층에서 아래 작업을 담당한다.

- 스냅샷 row를 API 응답 모델로 조립
- 시장별 섹션 배열 정렬
- 기사 링크 직렬화
- nullable 필드와 metadata 정리
- 내부 PK와 외부 노출 식별자 분리

---

## 4. 추천 폴더 구조

```text
app/
  main.py

  api/
    router.py
    deps/
      auth.py
      db.py
      request_context.py

  core/
    config.py
    logging.py
    exceptions.py
    response.py
    timezone.py

  db/
    base.py
    session.py
    enums.py
    models/
      batch_job.py
      batch_job_event.py
      news_article_raw.py
      news_article_processed.py
      news_cluster.py
      market_index_daily.py
      ai_summary.py
      market_daily_page.py
    repositories/
      batch_job_repo.py
      page_snapshot_repo.py
      cluster_repo.py
      article_repo.py
      index_repo.py
      ai_summary_repo.py

  schemas/
    common.py
    batch.py
    page.py
    cluster.py
    admin.py

  domains/
    pages/
      router.py
      service.py
      queries.py
      assembler.py
    archive/
      router.py
      service.py
      queries.py
    clusters/
      router.py
      service.py
      queries.py
      assembler.py
    batches/
      router.py
      service.py
      queries.py
      commands.py
    admin/
      router.py
      service.py

  batch/
    orchestrators/
      market_daily.py
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
    providers/
      news_provider.py
      market_index_provider.py
      llm_provider.py

tests/
  api/
  domains/
  repositories/
  batch/
```

---

## 5. 계층별 책임

### 5-1. `app/main.py`

- FastAPI 앱 생성
- 전역 예외 핸들러 등록
- 공통 미들웨어 등록
- 상위 API 라우터 연결

### 5-2. `app/api/`

역할:

- FastAPI dependency 정의
- 인증 정보 주입
- DB 세션 주입
- 요청 컨텍스트 생성
- 도메인 라우터 집계

주의:

- 비즈니스 로직을 두지 않는다.
- SQL 직접 접근을 두지 않는다.

### 5-3. `app/core/`

역할:

- 설정 로드
- 로깅 정책
- KST/UTC 시간 변환 규칙
- 공통 예외와 에러 코드
- API 공통 응답 envelope

특히 `timezone.py`는 다음 정책을 고정해야 한다.

- `business_date`는 KST 기준
- 저장 시각은 UTC 기반 `TIMESTAMPTZ`
- 응답 직렬화 시 포맷 일관성 유지

### 5-4. `app/db/models/`

역할:

- PostgreSQL DDL과 1:1 대응하는 ORM 모델 정의
- enum, relationship, FK, unique, index 메타데이터 표현

모델 분리 원칙:

- 테이블이 강하게 결합된 경우 한 파일에 묶는다.
- `market_daily_page*` 계열은 스냅샷 집합이므로 한 파일에 두는 것을 권장한다.

### 5-5. `app/db/repositories/`

역할:

- DB 접근 추상화
- 도메인 서비스가 사용할 쿼리 단위 메서드 제공

원칙:

- repository는 “테이블 CRUD”보다 “조회 패턴/사용 목적”을 우선한다.
- 복잡한 select와 eager loading은 repository에 둔다.
- FastAPI 응답 모델 조립은 repository가 아니라 assembler가 담당한다.

### 5-6. `app/schemas/`

역할:

- Pydantic 요청/응답 모델 정의
- API 문서와 직결되는 직렬화 계약 보관

범위:

- 공통 성공/실패 응답
- 페이지 응답
- 아카이브 목록 응답
- 배치 목록/상세 응답
- 클러스터 상세 응답

주의:

- DB 모델과 API 응답 모델을 혼용하지 않는다.

### 5-7. `app/domains/`

역할:

- 외부 API 명세에 대응되는 서비스 단위
- 라우터, 서비스, 쿼리, 직렬화 조립을 포함

핵심 원칙:

- 화면/API 단위로 책임을 나눈다.
- 각 도메인은 자기 응답 형식을 스스로 완성한다.

### 5-8. `app/batch/`

역할:

- 시장 일간 배치 오케스트레이션
- 단계별 실행과 상태 전이
- 외부 provider 호출
- 이벤트 로그 기록
- 최종 페이지 스냅샷 생성

주의:

- API 요청 서비스와 코드를 섞지 않는다.
- 배치에서 생성한 결과를 페이지 조회 API가 소비하는 방향으로만 연결한다.

---

## 6. 도메인별 설계

### 6-1. `domains/pages`

대상 API:

- `GET /stock/api/pages/daily/latest`
- `GET /stock/api/pages/daily`
- `GET /stock/api/pages/{pageId}`

책임:

- 최신 페이지 조회
- 날짜별 최신/특정 버전 조회
- 페이지 ID 기반 상세 조회
- 스냅샷 계층 데이터를 응답 모델로 조립

내부 구성:

- `router.py`
  - 입력 파라미터 검증
  - 서비스 호출
- `service.py`
  - 조회 흐름 제어
  - not found, version fallback 정책 처리
- `queries.py`
  - 페이지 조회에 필요한 repository 조합
- `assembler.py`
  - `market_daily_page*` row를 응답 DTO로 조립

### 6-2. `domains/archive`

대상 API:

- `GET /stock/api/pages/archive`

책임:

- 날짜 범위/상태 기준 목록 조회
- pagination 처리
- 아카이브 목록 응답 직렬화

설계 포인트:

- 최신 버전 페이지만 보여줄지 정책이 필요하다.
- 기본적으로는 `business_date`당 최신 `version_no`만 노출하는 것이 UI 요구와 잘 맞는다.

### 6-3. `domains/clusters`

대상 API:

- `GET /stock/api/news/clusters/{clusterId}`

책임:

- `cluster_uid` 기준 클러스터 상세 조회
- 대표 기사, 관련 기사, 분석 문단, 태그, 날짜 문맥 반환

설계 포인트:

- 외부 식별자는 UUID다.
- 내부 PK와 외부 `clusterId`를 구분해야 한다.
- 상세 응답은 `news_cluster`, `news_cluster_article`, `news_article_processed`, `ai_summary`를 조합할 수 있다.

### 6-4. `domains/batches`

대상 API:

- `GET /stock/api/batch/market-daily`
- `GET /stock/api/batch/jobs`
- `GET /stock/api/batch/jobs/{jobId}`

책임:

- 배치 실행 요청 수락
- 배치 목록 조회
- 배치 상세 조회
- 실행 중 중복 방지 처리

설계 포인트:

- `uq_batch_job_one_active_per_day` 제약과 애플리케이션 예외 처리를 함께 가져가야 한다.
- 목록 응답은 통계 요약까지 포함해야 한다.

### 6-5. `domains/admin`

대상 API:

- `POST /stock/api/admin/pages/rebuild`
- `GET /stock/api/admin/health`

책임:

- 강제 재생성 엔드포인트
- 서비스 헬스체크

설계 포인트:

- 운영성 엔드포인트이므로 인증/권한 경계를 명확히 둔다.

---

## 7. Repository 설계

### 7-1. `batch_job_repo.py`

주요 책임:

- 활성 배치 존재 여부 확인
- 배치 생성
- 배치 상태 업데이트
- 목록/상세 조회
- 집계 수치 계산

예상 메서드:

- `create_job(...)`
- `find_active_job_by_business_date(...)`
- `get_job_detail(job_id)`
- `list_jobs(filters, pagination)`
- `summarize_jobs(filters)`
- `mark_job_success(...)`
- `mark_job_partial(...)`
- `mark_job_failed(...)`

### 7-2. `page_snapshot_repo.py`

주요 책임:

- 최신 페이지 조회
- 날짜별 최신 버전 조회
- 특정 버전 조회
- 페이지 하위 섹션 일괄 로드
- 아카이브 목록 조회

예상 메서드:

- `get_latest_page()`
- `get_latest_page_by_business_date(business_date)`
- `get_page_by_business_date_and_version(business_date, version_no)`
- `get_page_by_id(page_id)`
- `list_archive_items(filters, pagination)`
- `create_page_snapshot(...)`

### 7-3. `cluster_repo.py`

주요 책임:

- `cluster_uid` 기준 클러스터 조회
- 관련 기사와 대표 기사 로드
- 클러스터 카드용 데이터 조회

예상 메서드:

- `get_cluster_detail_by_uid(cluster_uid)`
- `list_cluster_articles(cluster_id)`
- `list_top_clusters_by_business_date_and_market(...)`

### 7-4. `article_repo.py`

주요 책임:

- 원본/정제 기사 배치 처리
- dedupe 매핑 저장
- 기사 목록 조회 보조

### 7-5. `index_repo.py`

주요 책임:

- 시장별 대표 지수 저장
- 페이지 스냅샷용 지수 조회

### 7-6. `ai_summary_repo.py`

주요 책임:

- 글로벌/시장/클러스터 요약 저장
- 최신 요약 조회
- fallback 여부 추적

---

## 8. 배치 파이프라인 설계

### 8-1. 전체 흐름

`batch/orchestrators/market_daily.py`는 아래 순서를 관리한다.

1. `create_job`
2. `collect_news`
3. `dedupe_articles`
4. `build_clusters`
5. `collect_market_indices`
6. `generate_ai_summaries`
7. `build_page_snapshot`
8. `finalize_job`

### 8-2. 단계별 책임

#### `create_job.py`

- `business_date` 결정
- 중복 실행 방지
- 배치 row 생성
- 시작 이벤트 기록

#### `collect_news.py`

- 시장별 키워드 기반 수집
- `news_article_raw` 저장
- 수집 건수 집계

#### `dedupe_articles.py`

- 중복 제거
- 대표 제목/링크 정규화
- `news_article_processed`
- `news_article_raw_processed_map`

#### `build_clusters.py`

- 기사 클러스터링
- 대표 기사 확정
- `news_cluster`
- `news_cluster_article`

#### `collect_market_indices.py`

- 대표 지수 수집
- `market_index_daily` 저장

#### `generate_ai_summaries.py`

- 글로벌 헤드라인 생성
- 시장 요약 생성
- 클러스터 카드 요약 생성
- 클러스터 상세 분석 생성
- `ai_summary` 저장

#### `build_page_snapshot.py`

- 페이지 헤더 생성
- 시장별 스냅샷 생성
- 지수 카드 스냅샷 생성
- 클러스터 카드 스냅샷 생성
- 기사 링크 스냅샷 생성

#### `finalize_job.py`

- 상태 결정
- counts 반영
- `page_id`, `page_version_no` 연결
- partial/failed 메시지 반영
- 종료 이벤트 기록

### 8-3. 정책 계층

`batch/policies/`는 아래 책임을 가진다.

- `batch_status_policy.py`
  - 배치 상태 결정
- `page_status_policy.py`
  - 페이지 상태 결정
- `partial_failure_policy.py`
  - 부분 실패 기준 정의

이 계층을 따로 두는 이유는 다음과 같다.

- `READY`, `PARTIAL`, `FAILED` 기준이 문서상 정책 항목으로 남아 있다.
- 향후 운영 기준 변경 시 step 로직을 건드리지 않고 정책만 수정 가능해야 한다.

---

## 9. PostgreSQL 모델 매핑 원칙

### 9-1. enum 매핑

DB enum과 Python enum을 정확히 대응시킨다.

- `market_type_enum`
- `page_status_enum`
- `batch_job_status_enum`
- `batch_trigger_type_enum`
- `ai_summary_status_enum`
- `ai_summary_type_enum`
- `event_level_enum`

`app/db/enums.py`에 모아두는 것을 권장한다.

### 9-2. 스냅샷 모델 묶음

아래 테이블은 하나의 aggregate처럼 취급한다.

- `market_daily_page`
- `market_daily_page_market`
- `market_daily_page_market_index`
- `market_daily_page_market_cluster`
- `market_daily_page_article_link`

이유:

- 페이지 조회는 거의 항상 이 집합을 함께 읽는다.
- 조합 기준이 `page_id`와 `page_market_id`로 명확하다.
- 아키텍처상 “페이지 스냅샷 aggregate”로 보는 것이 자연스럽다.

### 9-3. 외부 식별자 처리

- 페이지 API는 내부 `page_id`를 사용 가능
- 클러스터 API는 외부 UUID `cluster_uid`를 사용

따라서 API schema와 ORM 모델에서 식별자 필드 전략을 분리해야 한다.

### 9-4. 시간 처리

원칙:

- 저장은 UTC 기반 `TIMESTAMPTZ`
- `business_date`는 KST 기준 `DATE`
- 응답 직렬화는 애플리케이션에서 일관성 있게 수행

`core/timezone.py`에 아래 함수를 두는 것을 권장한다.

- `get_kst_now()`
- `get_business_date(now=None)`
- `to_response_datetime(dt)`

---

## 10. 라우터 구성안

```python
from fastapi import APIRouter

from app.domains.pages.router import router as pages_router
from app.domains.archive.router import router as archive_router
from app.domains.clusters.router import router as clusters_router
from app.domains.batches.router import router as batches_router
from app.domains.admin.router import router as admin_router

api_router = APIRouter(prefix="/stock/api")
api_router.include_router(pages_router, prefix="/pages", tags=["pages"])
api_router.include_router(archive_router, prefix="/pages", tags=["archive"])
api_router.include_router(clusters_router, prefix="/news", tags=["news"])
api_router.include_router(batches_router, prefix="/batch", tags=["batch"])
api_router.include_router(admin_router, prefix="/admin", tags=["admin"])
```

주의:

- `archive`는 경로상 `/pages/archive`를 사용하므로 `pages`와 같은 prefix를 공유할 수 있다.
- 도메인 폴더와 URL prefix가 반드시 1:1일 필요는 없지만, 책임 경계는 유지해야 한다.

---

## 11. 초기 구현 우선순위

### 11-1. 1단계

목표:

- 앱 부트스트랩
- PostgreSQL 연결
- 공통 응답/예외 구조
- ORM 모델 뼈대

범위:

- `app/main.py`
- `app/core/*`
- `app/db/base.py`
- `app/db/session.py`
- `app/db/enums.py`
- `app/db/models/*`

### 11-2. 2단계

목표:

- 읽기 API 우선 구현

범위:

- `/pages/daily/latest`
- `/pages/daily`
- `/pages/archive`
- `/news/clusters/{clusterId}`

이유:

- Frontend 확인이 빠르다.
- 스냅샷 구조 검증이 가능하다.
- 배치 구현 전에도 fixture 기반 테스트가 쉽다.

### 11-3. 3단계

목표:

- 배치 조회/실행 API 구현

범위:

- `/batch/jobs`
- `/batch/jobs/{jobId}`
- `/batch/market-daily`

### 11-4. 4단계

목표:

- 배치 오케스트레이터 구현

범위:

- `batch/orchestrators/market_daily.py`
- `batch/steps/*`
- `batch/policies/*`

---

## 12. 테스트 전략

### 12-1. `tests/api/`

- 응답 코드
- 응답 스키마
- not found / conflict / validation 에러

### 12-2. `tests/domains/`

- 서비스 레벨 조립 로직
- 페이지 응답 assembler
- 클러스터 상세 assembler

### 12-3. `tests/repositories/`

- 최신 페이지 조회
- 특정 날짜/버전 조회
- 아카이브 목록 조회
- 배치 목록/상세 조회

### 12-4. `tests/batch/`

- step 단위 테스트
- partial/failure 정책 테스트
- 배치 상태 전이 테스트

---

## 13. 최종 권장안

PostgreSQL 기준으로 이 프로젝트는 아래 구조가 가장 적합하다.

1. API는 `domains/*` 단위의 vertical slice 구조로 나눈다.
2. DB 모델은 PostgreSQL DDL의 원천 계층과 스냅샷 계층을 그대로 반영한다.
3. 페이지와 클러스터 응답은 별도 assembler 계층에서 조립한다.
4. 배치는 `app/batch/` 아래에서 API와 분리된 오케스트레이션으로 구현한다.
5. 조회 API는 스냅샷 중심, 배치 파이프라인은 원천 데이터 중심으로 설계한다.

이 구조는 현재 PRD, API 명세, PostgreSQL DDL 설계와 가장 일관되며, 구현 이후에도 확장과 운영이 가장 수월하다.
