# 배치 스텝별 소요 시간 이력 설계

작성일: 2026-08-07

## 배경

배치 이력 상세 조회(`GET /batch/jobs/{jobId}`)는 잡 전체 소요 시간(`durationSeconds`)만 제공한다.
어느 스텝이 오래 걸렸는지 확인할 방법이 없어, 느린 잡의 병목을 사후에 특정할 수 없다.

현재 상태:

- 스텝 소요 시간은 `MarketDailyBatchOrchestrator`가 `perf_counter()`로 측정하지만
  애플리케이션 로그(`log_stage_event`)로만 나가고 DB에는 남지 않는다
  (`app/batch/orchestrators/market_daily.py:125,176`).
- DB에 남는 스텝 관련 데이터는 `batch_job_event`의 `created_at`과
  `batch_job.current_step`뿐이다. 전자는 스텝 시작/완료 이벤트가 오케스트레이터마다
  일관되지 않고, 후자는 현재 값만 유지한다.
- 상세 조회 응답(`build_batch_job_detail_payload`, `app/domains/batches/assembler.py:151`)에는
  스텝 관련 필드가 없다.

## 목표

배치 이력 상세 조회 응답에 각 스텝의 실행 구간과 소요 시간을 포함한다.
대상은 세 오케스트레이터 전부(market_daily, news_collection, ai_retry)다.

### 범위 밖

- 목록 조회(`GET /batch/jobs`) 응답 변경
- 스텝별 소요 시간 통계/집계 API
- 기존 잡 이력에 대한 소급 백필 (이력이 없는 잡은 빈 배열로 응답)

## 설계

### 1. 스키마 — `batch_job_step_run` 테이블 신설

```sql
CREATE TYPE batch_step_status_enum AS ENUM ('RUNNING', 'SUCCEEDED', 'FAILED');

CREATE TABLE batch_job_step_run (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_job_id BIGINT NOT NULL REFERENCES batch_job(id) ON DELETE CASCADE,
    step_code TEXT NOT NULL,
    seq INTEGER NOT NULL,
    status batch_step_status_enum NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at TIMESTAMPTZ NULL,
    duration_ms INTEGER NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_batch_job_step_run_job_seq UNIQUE (batch_job_id, seq),
    CONSTRAINT chk_batch_job_step_run_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_step_run_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX idx_batch_job_step_run_job_seq
    ON batch_job_step_run (batch_job_id, seq);
```

설계 결정:

- **시간 기준은 DB 시계(`now()`)**. 앱의 `perf_counter` 대신 DB에서 계산하므로
  잡 전체 `duration_seconds`와 기준이 같고, 워커가 죽어도 시작 시각이 남는다.
- **`seq`는 잡 내 실행 순번**. 표시 순서를 `started_at` 정렬에 의존하지 않고 확정한다.
  같은 밀리초에 시작한 스텝이 있어도 순서가 흔들리지 않는다.
- **재실행은 합치지 않는다**. 체크포인트 재개나 재시도로 같은 `step_code`가 다시 돌면
  행이 하나 더 생기고, 응답에도 별도 항목으로 나열된다. 재실행을 숨기면
  "왜 이 잡이 오래 걸렸나"라는 질문에 잘못된 답을 주게 된다.
- `ON DELETE CASCADE`로 잡 삭제 시 함께 정리된다.

`app/db/enums.py`에 대응 `BatchStepStatus(StrEnum)`을 추가한다.

### 2. 기록 지점 — `begin_step` 통합

`BatchJobRepository.begin_step`(`app/db/repositories/batch_job_repo.py:399`)은 세 오케스트레이터가
스텝 진입 시 모두 거치는 유일한 공통 지점이다
(`market_daily.py:128`, `news_collection.py:60`, `ai_retry/orchestrator.py:378`의 `_begin_step`).
여기에 계측을 넣는다.

#### `begin_step` 변경

리스 검증 UPDATE가 성공한 경우에만 `batch_job_step_run` 행을 INSERT하고,
반환 타입을 `bool` → `int | None`(생성된 step_run_id)로 바꾼다.
리스를 잃으면 행이 생기지 않으므로 유령 행이 남지 않는다.

`seq`는 같은 잡의 기존 최대값 + 1로 계산하며, INSERT 한 문장 안에서 서브쿼리로 구한다.
같은 잡을 두 워커가 동시에 진행하는 상황은 리스로 이미 배제되므로 경합은 발생하지 않고,
`uq_batch_job_step_run_job_seq`가 최후의 방어선 역할을 한다.

#### 신규 메서드

- `finish_step_run(step_run_id, *, status)`
  `ended_at = now()`, `duration_ms`를 DB에서 계산해 UPDATE.
  이미 마감된 행(`status <> 'RUNNING'`)은 갱신하지 않는다.
  계산식은 기존 `DURATION_SECONDS_EXPR` 옆(`app/db/repositories/sql_fragments.py:5`)에
  `STEP_DURATION_MS_EXPR` 상수로 추가해 정리 UPDATE와 공유한다.
- `list_step_runs(job_id)`
  `seq` 오름차순 조회.

#### 오케스트레이터 변경

세 오케스트레이터 모두 동일 패턴을 적용한다.

- 스텝 진입 시 `begin_step`이 돌려준 step_run_id를 보관한다.
- 스텝 성공 직후 `finish_step_run(..., status=SUCCEEDED)`를 호출한다.
  기존에 스텝 종료 시점에 이미 커밋이 일어나므로 별도 커밋을 추가하지 않는다.
- 예외 경로에서 진행 중인 step_run이 있으면 `finish_step_run(..., status=FAILED)`로 마감한다.
  실패 경로는 rollback 후 진단 이벤트를 쓰고 커밋하는 기존 흐름이 있으므로 거기에 얹는다.

`market_daily`에서 체크포인트로 건너뛴 스텝(`completed_steps`에 포함된 스텝)은
`begin_step`을 호출하지 않으므로 이번 실행의 이력에 나타나지 않는다.
해당 스텝의 이력은 그 스텝을 실제로 실행한 이전 시도의 잡 이력에 남아 있다.

#### 유실된 행 정리

워커가 통째로 죽으면 `RUNNING` 상태의 step_run이 남는다.
`recover_expired_claims`(`batch_job_repo.py:466`)에 정리 UPDATE를 추가한다.

```sql
UPDATE batch_job_step_run sr
SET status = 'FAILED',
    ended_at = now(),
    duration_ms = GREATEST(
        (EXTRACT(EPOCH FROM (now() - sr.started_at)) * 1000)::int, 0
    ),
    updated_at = now()
FROM batch_job j
WHERE sr.batch_job_id = j.id
  AND sr.status = 'RUNNING'
  AND j.status <> 'RUNNING'
```

잡이 더 이상 RUNNING이 아닌데 스텝만 RUNNING인 상태를 모두 정리하므로,
리스 만료 재큐잉과 시도 소진 실패를 한 번에 커버하고 자가 치유된다.

### 3. API 응답

`app/schemas/batch.py`:

```python
class BatchJobStepRunResponse(BaseModel):
    stepCode: str
    status: str
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationMs: int | None = None


class BatchJobDetailResponse(BaseModel):
    ...
    steps: list[BatchJobStepRunResponse] = []
```

`BatchesService.get_job_detail`(`app/domains/batches/service.py:178`)에서
`list_step_runs`를 함께 조회해 `build_batch_job_detail_payload`에 넘기고,
assembler에서 매핑한다.

응답 예시:

```json
{
  "jobId": 42,
  "durationSeconds": 187,
  "steps": [
    { "stepCode": "CREATE_JOB", "status": "SUCCEEDED",
      "startedAt": "2026-08-07T09:00:00+09:00",
      "endedAt": "2026-08-07T09:00:00+09:00", "durationMs": 12 },
    { "stepCode": "DEDUPE_ARTICLES", "status": "SUCCEEDED",
      "startedAt": "2026-08-07T09:00:00+09:00",
      "endedAt": "2026-08-07T09:00:04+09:00", "durationMs": 4210 }
  ]
}
```

하위 호환: 기존 필드는 그대로 유지되고 `steps`만 추가된다.
스텝 이력이 없는 과거 잡은 `steps: []`로 응답한다.

### 4. 알려진 제약

`AiRetryOrchestrator`는 재처리 대상마다 `AI_RETRY_GENERATE_STEP`으로 `begin_step`을 호출한다
(`app/batch/ai_retry/orchestrator.py:125`, 대상 수만큼 반복).
따라서 AI 재처리 잡의 `steps` 배열에는 `GENERATE` 항목이 대상 수만큼 들어간다.
대상별 생성 지연을 그대로 보여주는 편이 진단에 유용하므로 합치지 않으며,
이는 "재실행을 합치지 않는다"는 결정과 동일한 방향이다.


`begin_step`은 `lease_token`이 있을 때만 호출된다
(`market_daily.py:127`의 `if lease_token is not None`).
리스 없이 오케스트레이터를 직접 호출하는 경로에서는 스텝 이력이 남지 않는다.
실제 워커 실행 경로는 항상 리스를 갖고 진입하므로 운영상 문제가 되지 않는다.

## 테스트

- **리포지토리** (`tests/repositories/test_batch_job_repo.py`)
  - `begin_step`이 step_run 행을 만들고 id를 반환한다
  - 리스가 유효하지 않으면 `None`을 반환하고 행을 만들지 않는다
  - 같은 잡의 두 번째 `begin_step`은 `seq`가 증가한다
  - `finish_step_run`이 `ended_at`/`duration_ms`/`status`를 채운다
  - 이미 마감된 행은 `finish_step_run`으로 갱신되지 않는다
  - `list_step_runs`가 `seq` 순으로 반환한다
  - `recover_expired_claims`가 고아 `RUNNING` step_run을 `FAILED`로 마감한다
- **오케스트레이터** (`tests/batch/test_market_daily_orchestrator.py`,
  `test_news_collection_orchestrator.py`, `test_ai_retry_orchestrator.py`)
  - 정상 완료 시 각 스텝이 `SUCCEEDED`로 마감된다
  - 스텝 실행 중 예외 발생 시 해당 step_run이 `FAILED`로 마감된다
  - 기존 fake repository 3곳(`test_market_daily_orchestrator.py:319,411`,
    `test_news_collection_orchestrator.py:61`)의 `begin_step` 시그니처를 갱신한다
- **응답 조립**
  - 상세 응답에 `steps`가 `seq` 순으로 담긴다
  - 이력이 없으면 `[]`가 담긴다
- **마이그레이션**
  - `db/alembic/README.md`의 규약을 따른다: `alembic/versions/`에 신규 전진 revision을 추가하고
    (`down_revision = '20260731_00_baseline'`), 동일한 목표 상태를
    `db/schema_postgresql.sql`에 반영한다. 기존 revision과 동결 베이스라인 자산
    (`db/alembic/baselines/`, `db/migrations/`)은 수정하지 않는다.
  - upgrade/downgrade 양방향 동작 확인
