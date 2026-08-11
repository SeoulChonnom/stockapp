# 미해결 과제

`docs/architecture_code_quality_audit.md`(2026-05-14)와
`docs/usability_and_risk_analysis.md`(2026-07-14) 두 감사 보고서를 2026-08-11에
현재 코드 기준으로 재검증한 결과, 지적 사항 대부분이 해소되었다. 두 보고서는
삭제했고 아직 유효한 항목만 아래에 남긴다. 원문은 git 히스토리에서 확인할 수 있다.

## 1. 운영 환경 CORS 미등록 — 배포 정책 결정 대기

`app/main.py:63`은 `settings.is_development`일 때만 `CORSMiddleware`를 등록한다.
운영 프론트엔드가 별도 origin에서 API를 직접 호출하는지, 동일 origin
reverse proxy를 쓰는지 확인해야 정책을 정할 수 있다. 확인 없이 CORS를 넓히면
보안 경계가 바뀐다.

## 2. `auth_stub_token` 죽은 설정

`app/core/settings.py:127`의 `auth_stub_token: str = 'dev-token'`은 인증 경로에서
쓰이지 않는다. 현재 유일한 참조는 `app/core/error_diagnostics.py:130`의 마스킹
대상 목록이다. 제거하려면 이 마스킹 항목과 외부 호출자 호환성을 함께 확인해야 한다.

## 3. 뉴스 원문 적재가 row-by-row INSERT

`NewsArticleRawRepository.insert_articles`
(`app/db/repositories/news_article_raw_repo.py:176`)는 기사 수만큼 `execute`를
반복한다. `ON CONFLICT ... DO NOTHING ... RETURNING id`로 삽입 건수를 세는
구조라 단순 치환은 어렵다. 수집량이 늘면 executemany 또는 다중 VALUES + 반환
행 집계 방식으로 묶는 것을 검토한다.

---

## 재검증에서 해소가 확인된 주요 항목

| 원문 지적 | 현재 상태 |
| --- | --- |
| 도메인 서비스가 `app.api.deps`/응답 schema에 결합 | `app/domains/*/service.py`에 API 계층 import 없음 |
| 라우터 prefix 소유권 분산 | 각 도메인 router가 자체 prefix/tags 소유 |
| SQL identifier raw 보간 | `app/db/identifiers.py`가 정규식 검증 + quoting 담당, `search_path`도 경유 |
| 배치 commit 경계 분산 | 저장소 commit 2곳으로 축소, 오케스트레이터/워커가 소유 |
| 외부 API 실패 은닉 | `last_failures`, `failure_details`로 provider/에러 클래스/메시지 보존 |
| `hasattr(session, 'bind')` 실행 모드 분기 | 코드에서 제거됨 |
| router 중복 validation | pages router의 `payload is None` 분기 제거 |
| JWT secret 형식 계약 불명확 | `.env.example`, README에 base64url·32바이트 명시 + 기동 검증 |
| LLM/크롤링 순차 처리 | enrich·summary·본문 fetch에 semaphore 동시성 적용 |
| provider 실패 경로 테스트 부족 | `tests/batch/test_*_provider.py` 4종 존재 |
| 스키마 중복·migration 도구 부재 | Alembic 도입 (`alembic/versions/`, 기동 시 자동 적용) |
| health/readiness | `/stock/api/health`가 DB 검사 포함, `/ready` 제거 |
