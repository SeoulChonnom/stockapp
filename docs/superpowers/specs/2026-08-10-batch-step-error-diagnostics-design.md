# 배치 스텝 오류 진단 이력 설계

작성일: 2026-08-10

## 배경

`batch_job_step_run`은 실행 순서, 상태, 시작·종료 시각과 실행시간을 보존하지만
실패 원인은 저장하지 않는다. 같은 스텝이 재시도되면 과거 `FAILED` 행과 소요 시간은
남아도 당시 오류를 상세 조회에서 확인할 수 없다.

## 목표

- 실패한 스텝 실행 행에 운영자용 오류 메시지와 진단 로그를 저장한다.
- `GET /stock/api/batch/jobs/{jobId}`의 `steps[]`에서 두 값을 반환한다.
- 진단 로그는 전체 Python traceback을 기반으로 하되 저장 전에 민감정보를 마스킹한다.

## 범위 밖

- 잡 또는 스텝에 `attempt_no` 추가
- 목록 조회 응답 변경
- 기존 스텝 실행 행의 오류 정보 백필
- 마스킹 전 원본 오류 로그 저장 또는 복원 기능
- 별도 오류 로그 테이블이나 암호화 키 관리

## 설계

### 1. 스키마

`batch_job_step_run`에 nullable `TEXT` 컬럼 두 개를 추가한다.

```sql
ALTER TABLE batch_job_step_run
    ADD COLUMN error_message TEXT NULL,
    ADD COLUMN error_log TEXT NULL;
```

- `error_message`: 운영자 화면에 바로 표시할 수 있는 정제된 오류 요약
- `error_log`: 저장 전에 마스킹된 전체 traceback
- 성공하거나 아직 실행 중인 행은 두 컬럼 모두 `NULL`
- 재시도는 기존 방식대로 새 행을 만들므로 이전 실패 행의 오류 정보가 보존된다.

신규 Alembic 전진 revision을 추가하고 `db/schema_postgresql.sql`의 기준 스키마에도
같은 컬럼을 반영한다. downgrade는 두 컬럼만 제거한다.

### 2. 오류 진단 생성과 마스킹

오류 진단 생성은 DB repository와 분리된 작은 공용 함수로 둔다. 입력은
`BaseException`, 출력은 `error_message`와 `error_log`다.

`error_message`는 기존 공개 진단 정책을 재사용해 provider 응답이나 인증정보가
노출되지 않는 짧은 메시지로 만든다. 이미 안전한 도메인 오류 메시지가 있으면 이를
보존하고, 알 수 없는 예외는 일반화된 배치 단계 실패 메시지로 바꾼다.

`error_log`는 `traceback.format_exception(..., chain=True)`로 예외 체인을 포함해 만든 뒤
아래 순서로 마스킹한다.

1. 현재 `Settings`의 비밀 설정값과 정확히 일치하는 문자열을 `[REDACTED]`로 치환한다.
   대상은 DB URL의 사용자명·비밀번호, JWT secret, 인증 stub token,
   Naver client secret, Gemini API key다. 비어 있거나 지나치게 짧은 값은
   값 기반 치환 대상에서 제외한다.
2. 설정에 없는 외부 비밀값을 막기 위해 대소문자 비구분 패턴을 적용한다.
   - `Authorization`, `Bearer`, `Basic` 인증값
   - `password`, `passwd`, `api_key`, `apikey`, `token`, `secret`,
     `client_secret`, `credential`의 `key=value`, `key: value`, JSON 표현
   - URL의 `scheme://user:password@host` 중 사용자 정보
   - JWT 형태의 세 구간 토큰
3. 마스킹된 로그는 최대 32 KiB로 제한한다. 초과하면 앞부분과 마지막 예외 부분을
   함께 남기고 중앙에 truncation 표식을 넣어 발생 위치와 최종 원인을 모두 보존한다.

로컬 변수는 traceback에 포함하지 않는다. 마스킹은 DB 저장 전에 수행하고,
상세 응답 조립 시에도 동일 함수를 한 번 더 적용해 방어적으로 보장한다.
마스킹되지 않은 원본은 DB, 이벤트 또는 API payload에 남기지 않는다.

### 3. 스텝 종료 흐름

`BatchJobRepository.finish_step_run`을 다음 계약으로 확장한다.

```python
async def finish_step_run(
    *,
    step_run_id: int,
    status: str,
    error_message: str | None = None,
    error_log: str | None = None,
) -> bool:
    ...
```

- `SUCCEEDED`: 두 오류 컬럼을 `NULL`로 기록한다.
- `FAILED`: 오케스트레이터가 생성한 정제 메시지와 마스킹 로그를 기록한다.
- 이미 종료된 행은 현재와 같이 변경하지 않는다.

Market Daily, News Collection, AI Retry 오케스트레이터의 예외 처리 지점에서
동일한 진단 생성 함수를 호출해 실패한 현재 step-run을 종료한다.

worker 프로세스 중단 후 `recover_expired_claims`가 고아 `RUNNING` 행을 정리하는 경우에는
Python 예외 객체가 없으므로 고정 메시지와 리스 만료·worker 중단을 나타내는 구조화된
진단 문자열을 기록한다.

### 4. 조회 및 API

`BatchJobStepRunRecord`, repository SELECT, Pydantic 응답 모델과 assembler에
두 필드를 추가한다.

```json
{
  "stepCode": "COLLECT_NEWS",
  "status": "FAILED",
  "startedAt": "2026-08-10T09:00:00+09:00",
  "endedAt": "2026-08-10T09:00:03+09:00",
  "durationMs": 3200,
  "errorMessage": "External provider request failed.",
  "errorLog": "Traceback (most recent call last): ... [REDACTED]"
}
```

기존 및 성공 행은 `errorMessage: null`, `errorLog: null`로 응답한다. 엔드포인트는
기존 `BatchOperatorDep` 권한을 유지하지만, 권한만 신뢰하지 않고 항상 마스킹된 값만
반환한다.

### 5. 테스트

- 마스킹 단위 테스트
  - 설정에 존재하는 각 비밀값
  - Bearer/Basic, 키-값, JSON, URL 사용자 정보, JWT 패턴
  - 비민감 traceback 정보 보존
  - 예외 체인과 32 KiB 제한
- repository 테스트
  - 실패 종료 시 두 오류 컬럼 저장
  - 성공 종료 시 두 오류 컬럼 `NULL`
  - 목록 조회 projection 매핑
  - 고아 step-run 정리 시 고정 진단 저장
- 오케스트레이터 테스트
  - 각 오케스트레이터의 실패 경로가 마스킹된 진단을 전달
  - 성공 경로의 기존 동작 유지
- API/assembler 테스트
  - `steps[].errorMessage`와 `steps[].errorLog` 반환
  - 성공 및 기존 행에서 `null`
  - API 직전 방어적 재마스킹
- Alembic upgrade/downgrade와 기준 스키마 일치 확인

## 선택한 접근과 트레이드오프

하이브리드 쓰기 시점 마스킹을 선택했다. 구조화된 안전 프레임만 저장하는 방식보다
진단 정보가 풍부하고, 암호화된 원본을 별도로 저장하는 방식보다 구현과 운영이 단순하다.
정규식으로 모든 미지의 민감정보를 완벽히 탐지할 수는 없으므로 설정값 기반 치환과
API 직전 재마스킹을 함께 적용한다.
