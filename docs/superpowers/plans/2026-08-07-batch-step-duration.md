# 배치 스텝별 소요 시간 이력 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 배치 이력 상세 조회(`GET /batch/jobs/{jobId}`) 응답에 각 스텝의 실행 구간과 소요 시간을 포함시킨다.

**Architecture:** `batch_job_step_run` 테이블을 신설하고, 세 오케스트레이터가 공통으로 거치는 `BatchJobRepository.begin_step`에서 행을 INSERT한다. 스텝 종료 시 `finish_step_run`으로 마감하며, 시간은 모두 DB 시계(`now()`) 기준으로 계산한다. 상세 조회 서비스가 `list_step_runs`를 함께 읽어 응답에 `steps` 배열로 내려준다.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, SQLAlchemy(Core `text()` 기반 raw SQL), Alembic, PostgreSQL, pytest(+anyio)

**Spec:** `docs/superpowers/specs/2026-08-07-batch-step-duration-design.md`

## Global Constraints

- 코드베이스 규약: 모든 모듈은 `from __future__ import annotations`로 시작하고, 파일 끝에 `__all__`을 정의한다. 문자열은 홑따옴표를 쓴다.
- 리포지토리는 SQLAlchemy ORM이 아니라 `text()` raw SQL을 쓴다. 테이블명은 반드시 `qualify_db_identifier('테이블명')`으로 스키마 한정한다.
- 리포지토리 단위 테스트는 실제 DB를 쓰지 않는다. `tests/support.py`의 `RecordingAsyncSession` / `DummyResult`로 세션을 대역화하고, 실행된 SQL 문자열과 파라미터를 검증한다.
- 마이그레이션 규약(`db/alembic/README.md`): `alembic/versions/`에 신규 전진 revision을 추가하고 동일한 목표 상태를 `db/schema_postgresql.sql`에 반영한다. 기존 revision과 `db/alembic/baselines/`, `db/migrations/`의 동결 자산은 절대 수정하지 않는다.
- 현재 alembic head는 `20260731_00_baseline`이다.
- 스텝 상태 값은 `RUNNING` / `SUCCEEDED` / `FAILED` 세 가지다. 표기를 임의로 바꾸지 않는다.
- 테스트 실행 명령은 `uv run pytest <경로> -v`다.
- 린트는 `uv run ruff check .` / `uv run ruff format --check .`로 확인한다.

## 파일 구조

| 파일 | 역할 | 신규/수정 |
| --- | --- | --- |
| `alembic/versions/20260807_01_batch_job_step_run.py` | 테이블/ENUM 생성 마이그레이션 | 신규 |
| `db/schema_postgresql.sql` | 목표 스키마 동기화 | 수정 |
| `app/db/enums.py` | `BatchStepStatus` 추가 | 수정 |
| `app/db/repositories/sql_fragments.py` | `STEP_DURATION_MS_EXPR` 추가 | 수정 |
| `app/db/repositories/projections.py` | `BatchJobStepRunRecord` 추가 | 수정 |
| `app/db/repositories/batch_job_repo.py` | `begin_step` 변경, `finish_step_run`/`list_step_runs` 추가, 고아 행 정리 | 수정 |
| `app/batch/orchestrators/market_daily.py` | step_run 마감 연결 | 수정 |
| `app/batch/orchestrators/news_collection.py` | step_run 마감 연결 | 수정 |
| `app/batch/ai_retry/orchestrator.py` | step_run 마감 연결 | 수정 |
| `app/schemas/batch.py` | `BatchJobStepRunResponse`, `BatchJobDetailResponse.steps` | 수정 |
| `app/domains/batches/assembler.py` | `steps` 매핑 | 수정 |
| `app/domains/batches/service.py` | `list_step_runs` 조회 연결 | 수정 |
| `docs/api_spec_doc.md` | 상세 응답 예시 갱신 | 수정 |

작업 순서는 아래에서 위로 의존한다: 스키마(Task 1) → 리포지토리(Task 2~4) → 오케스트레이터(Task 5~7) → API 응답(Task 8~9) → 문서(Task 10).

---

### Task 1: 스키마 마이그레이션과 상태 ENUM

**Files:**
- Create: `alembic/versions/20260807_01_batch_job_step_run.py`
- Modify: `db/schema_postgresql.sql:172` 근처 (`batch_job_event` 정의 뒤)
- Modify: `app/db/enums.py:54` 근처 (`EventLevel` 뒤)
- Test: `tests/db/test_batch_step_status_enum.py`

**Interfaces:**
- Consumes: 없음 (첫 작업)
- Produces: `stock.batch_job_step_run` 테이블, `batch_step_status_enum` 타입, `app.db.enums.BatchStepStatus` (`RUNNING` / `SUCCEEDED` / `FAILED`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/db/test_batch_step_status_enum.py` 생성:

```python
from __future__ import annotations

from app.db.enums import BatchStepStatus


def test_batch_step_status_members_match_database_enum():
    assert [status.value for status in BatchStepStatus] == [
        'RUNNING',
        'SUCCEEDED',
        'FAILED',
    ]


def test_batch_step_status_is_string_comparable():
    assert BatchStepStatus.SUCCEEDED == 'SUCCEEDED'
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/db/test_batch_step_status_enum.py -v`
Expected: FAIL — `ImportError: cannot import name 'BatchStepStatus'`

- [ ] **Step 3: ENUM 추가**

`app/db/enums.py`의 `EventLevel` 클래스 바로 뒤에 추가:

```python
class BatchStepStatus(StrEnum):
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/db/test_batch_step_status_enum.py -v`
Expected: PASS

- [ ] **Step 5: `db/schema_postgresql.sql` 갱신**

`CREATE TYPE event_level_enum ...` 줄(파일 상단 타입 선언 블록 끝) 바로 뒤에 추가:

```sql
CREATE TYPE batch_step_status_enum AS ENUM ('RUNNING', 'SUCCEEDED', 'FAILED');
```

`CREATE INDEX idx_batch_job_event_job_created ...` 문장 뒤(`CREATE TABLE news_search_keyword` 앞)에 추가:

```sql
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
    CONSTRAINT chk_batch_job_step_run_seq_positive
        CHECK (seq >= 1),
    CONSTRAINT chk_batch_job_step_run_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_step_run_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX idx_batch_job_step_run_job_seq
    ON batch_job_step_run (batch_job_id, seq);
```

- [ ] **Step 6: alembic revision 작성**

`alembic/versions/20260807_01_batch_job_step_run.py` 생성:

```python
"""Add batch_job_step_run to record per-step execution durations.

Revision ID: 20260807_01_step_run
Revises: 20260731_00_baseline
Create Date: 2026-08-07
"""

from alembic import op

revision = '20260807_01_step_run'
down_revision = '20260731_00_baseline'
branch_labels = None
depends_on = None

_UPGRADE_SQL = """
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
    CONSTRAINT chk_batch_job_step_run_seq_positive
        CHECK (seq >= 1),
    CONSTRAINT chk_batch_job_step_run_ended_after_started
        CHECK (ended_at IS NULL OR ended_at >= started_at),
    CONSTRAINT chk_batch_job_step_run_duration_non_negative
        CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX idx_batch_job_step_run_job_seq
    ON batch_job_step_run (batch_job_id, seq);
"""

_DOWNGRADE_SQL = """
DROP TABLE IF EXISTS batch_job_step_run;
DROP TYPE IF EXISTS batch_step_status_enum;
"""


def upgrade() -> None:
    op.execute(_UPGRADE_SQL)


def downgrade() -> None:
    op.execute(_DOWNGRADE_SQL)
```

주의: `alembic/env.py`가 이미 `search_path`를 설정하므로 SQL 안에서 스키마를 다시 한정하지 않는다. 베이스라인 revision(`20260731_00_current_schema_baseline.py`)이 같은 방식으로 스키마 비한정 SQL을 실행하는 것을 참고하라.

- [ ] **Step 7: alembic head가 하나인지 확인**

Run: `uv run alembic heads`
Expected: `20260807_01_step_run (head)` 한 줄만 출력

- [ ] **Step 8: 전체 테스트와 린트**

Run: `uv run pytest tests -q && uv run ruff check . && uv run ruff format --check .`
Expected: 전부 PASS

- [ ] **Step 9: 커밋**

```bash
git add alembic/versions/20260807_01_batch_job_step_run.py db/schema_postgresql.sql app/db/enums.py tests/db/test_batch_step_status_enum.py
git commit -m "feat: 배치 스텝 실행 이력 테이블 batch_job_step_run 추가"
```

---

### Task 2: `begin_step`이 스텝 실행 이력을 남기도록 변경

**Files:**
- Modify: `app/db/repositories/sql_fragments.py:5`
- Modify: `app/db/repositories/batch_job_repo.py:399-428` (`begin_step`)
- Test: `tests/repositories/test_batch_job_repo.py`

**Interfaces:**
- Consumes: Task 1의 `batch_job_step_run` 테이블
- Produces:
  - `STEP_DURATION_MS_EXPR: str` (in `app/db/repositories/sql_fragments.py`)
  - `BatchJobRepository.begin_step(*, job_id: int, lease_token: UUID, step_code: str) -> int | None`
    — 리스가 유효하면 새 step_run id, 아니면 `None`. **반환 타입이 `bool`에서 바뀌는 파괴적 변경이다.**

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/repositories/test_batch_job_repo.py` 끝에 추가:

```python
@pytest.mark.anyio
async def test_begin_step_records_step_run_and_returns_its_id():
    lease_token = uuid4()
    session = RecordingAsyncSession(
        results=[DummyResult([1001]), DummyResult([777])]
    )
    repo = BatchJobRepository(session)

    step_run_id = await repo.begin_step(
        job_id=1001,
        lease_token=lease_token,
        step_code='COLLECT_NEWS',
    )

    assert step_run_id == 777
    lease_sql = normalize_sql(session.statements[0]).lower()
    assert 'update stock.batch_job' in lease_sql
    assert 'lease_expires_at > now()' in lease_sql
    insert_sql = normalize_sql(session.statements[1]).lower()
    assert 'insert into stock.batch_job_step_run' in insert_sql
    assert 'coalesce(max(seq), 0) + 1' in insert_sql
    assert session.parameters[1] == {
        'job_id': 1001,
        'step_code': 'COLLECT_NEWS',
    }


@pytest.mark.anyio
async def test_begin_step_returns_none_and_skips_insert_when_lease_lost():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = BatchJobRepository(session)

    step_run_id = await repo.begin_step(
        job_id=1001,
        lease_token=uuid4(),
        step_code='COLLECT_NEWS',
    )

    assert step_run_id is None
    assert len(session.statements) == 1
```

- [ ] **Step 2: 기존 테스트의 단언을 새 계약에 맞춰 수정**

`tests/repositories/test_batch_job_repo.py:433`의 `test_heartbeat_and_checkpoint_updates_are_lease_fenced`는 `begin_step`이 `True`를 반환한다고 단언한다. `begin_step`이 이제 SQL을 두 번 실행하므로 결과 큐와 단언, 인덱스를 함께 고친다.

- `RecordingAsyncSession(results=[...])`를 `[DummyResult([1001]), DummyResult([1001]), DummyResult([777]), DummyResult([1001])]`로 바꾼다 (heartbeat / begin_step 리스 검증 / begin_step INSERT / save_checkpoint 순서).
- `assert began is True`를 `assert began == 777`로 바꾼다.
- `checkpoint_sql`이 참조하는 `session.statements[2]`를 `session.statements[3]`으로 바꾸고, `session.parameters[2]`를 `session.parameters[3]`으로 바꾼다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v -k "begin_step or lease_fenced"`
Expected: FAIL — 새 테스트 2개는 `step_run_id == 777` 단언에서, 수정한 기존 테스트는 인덱스 불일치로 실패

- [ ] **Step 4: `STEP_DURATION_MS_EXPR` 추가**

`app/db/repositories/sql_fragments.py`의 `DURATION_SECONDS_EXPR` 아래에 추가하고 `__all__`도 갱신:

```python
STEP_DURATION_MS_EXPR = (
    'GREATEST((EXTRACT(EPOCH FROM (now() - started_at)) * 1000)::int, 0)'
)
```

```python
__all__ = [
    'DURATION_SECONDS_EXPR',
    'STEP_DURATION_MS_EXPR',
    'lease_null_assignments_sql',
]
```

- [ ] **Step 5: `begin_step` 구현 변경**

`app/db/repositories/batch_job_repo.py`의 `begin_step`을 아래로 교체한다. 리스 검증 UPDATE는 그대로 두고, 성공했을 때만 INSERT를 한 번 더 실행한다.

```python
    async def begin_step(
        self,
        *,
        job_id: int,
        lease_token: UUID,
        step_code: str,
    ) -> int | None:
        statement = text(
            """
            UPDATE {batch_job_table}
            SET current_step = :step_code, updated_at = now()
            WHERE id = :job_id
              AND status = '{status_running}'
              AND lease_token = :lease_token
              AND lease_expires_at > now()
            RETURNING id
            """.format(
                batch_job_table=qualify_db_identifier('batch_job'),
                status_running=_STATUS_RUNNING,
            )
        )
        result = await self.session.execute(
            statement,
            {
                'job_id': job_id,
                'lease_token': lease_token,
                'step_code': step_code,
            },
        )
        if result.scalar_one_or_none() is None:
            return None
        return await self._insert_step_run(job_id=job_id, step_code=step_code)

    async def _insert_step_run(self, *, job_id: int, step_code: str) -> int:
        statement = text(
            """
            INSERT INTO {step_run_table} (batch_job_id, step_code, seq, status)
            SELECT
                :job_id,
                :step_code,
                COALESCE(MAX(seq), 0) + 1,
                '{status_running}'
            FROM {step_run_table}
            WHERE batch_job_id = :job_id
            RETURNING id
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
                status_running=BatchStepStatus.RUNNING.value,
            )
        )
        result = await self.session.execute(
            statement,
            {'job_id': job_id, 'step_code': step_code},
        )
        return int(result.scalar_one())
```

`app/db/repositories/batch_job_repo.py` 상단 import를 갱신한다:

```python
from app.db.enums import BatchJobStatus, BatchJobType, BatchRunMode, BatchStepStatus
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v`
Expected: PASS

`seq`가 실제로 증가하는지는 DB가 계산하는 값이라 대역 세션으로는 검증할 수 없다. 대신 INSERT SQL에 `COALESCE(MAX(seq), 0) + 1`이 들어 있는지와 `uq_batch_job_step_run_job_seq` 제약(Task 1)이 그 계약을 강제한다.

- [ ] **Step 7: 커밋**

```bash
git add app/db/repositories/sql_fragments.py app/db/repositories/batch_job_repo.py tests/repositories/test_batch_job_repo.py
git commit -m "feat: begin_step이 스텝 실행 이력 행을 생성하고 id를 반환"
```

이 시점에는 오케스트레이터가 아직 `bool` 계약을 가정하므로 오케스트레이터 테스트가 깨져 있을 수 있다. Task 5~7에서 정리한다. 커밋 전 `uv run pytest tests -q`가 실패하면 실패 목록이 오케스트레이터 관련인지만 확인하고 진행한다.

---

### Task 3: `finish_step_run`으로 스텝 마감

**Files:**
- Modify: `app/db/repositories/batch_job_repo.py` (`begin_step` 바로 아래)
- Test: `tests/repositories/test_batch_job_repo.py`

**Interfaces:**
- Consumes: Task 2의 `STEP_DURATION_MS_EXPR`, `BatchStepStatus`
- Produces: `BatchJobRepository.finish_step_run(*, step_run_id: int, status: str) -> bool`

- [ ] **Step 1: 실패하는 테스트 작성**

```python
@pytest.mark.anyio
async def test_finish_step_run_closes_running_row_with_duration():
    session = RecordingAsyncSession(results=[DummyResult([777])])
    repo = BatchJobRepository(session)

    finished = await repo.finish_step_run(step_run_id=777, status='SUCCEEDED')

    assert finished is True
    sql = normalize_sql(session.statements[0]).lower()
    assert 'update stock.batch_job_step_run' in sql
    assert 'ended_at = now()' in sql
    assert "where id = :step_run_id and status = 'running'" in sql
    assert session.parameters[0] == {
        'step_run_id': 777,
        'status': 'SUCCEEDED',
    }


@pytest.mark.anyio
async def test_finish_step_run_returns_false_when_already_closed():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = BatchJobRepository(session)

    finished = await repo.finish_step_run(step_run_id=777, status='FAILED')

    assert finished is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v -k finish_step_run`
Expected: FAIL — `AttributeError: 'BatchJobRepository' object has no attribute 'finish_step_run'`

- [ ] **Step 3: 구현**

`app/db/repositories/batch_job_repo.py`의 `_insert_step_run` 뒤에 추가:

```python
    async def finish_step_run(self, *, step_run_id: int, status: str) -> bool:
        statement = text(
            """
            UPDATE {step_run_table}
            SET
                status = CAST(:status AS batch_step_status_enum),
                ended_at = now(),
                duration_ms = {step_duration_ms_expr},
                updated_at = now()
            WHERE id = :step_run_id
              AND status = '{status_running}'
            RETURNING id
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
                step_duration_ms_expr=STEP_DURATION_MS_EXPR,
                status_running=BatchStepStatus.RUNNING.value,
            )
        )
        result = await self.session.execute(
            statement,
            {'step_run_id': step_run_id, 'status': status},
        )
        return result.scalar_one_or_none() is not None
```

import에 `STEP_DURATION_MS_EXPR`를 추가한다:

```python
from app.db.repositories.sql_fragments import (
    DURATION_SECONDS_EXPR,
    STEP_DURATION_MS_EXPR,
    lease_null_assignments_sql,
)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v -k finish_step_run`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add app/db/repositories/batch_job_repo.py tests/repositories/test_batch_job_repo.py
git commit -m "feat: finish_step_run으로 스텝 실행 이력 마감"
```

---

### Task 4: 조회 메서드와 고아 행 정리

**Files:**
- Modify: `app/db/repositories/projections.py` (`BatchLeaseRecoveryResult` 정의 근처)
- Modify: `app/db/repositories/batch_job_repo.py` (`recover_expired_claims`, 신규 `list_step_runs`)
- Test: `tests/repositories/test_batch_job_repo.py`

**Interfaces:**
- Consumes: Task 3의 `STEP_DURATION_MS_EXPR`, `BatchStepStatus`
- Produces:
  - `BatchJobStepRunRecord` 데이터클래스 — 필드: `step_run_id: int`, `step_code: str`, `seq: int`, `status: str`, `started_at: datetime`, `ended_at: datetime | None`, `duration_ms: int | None`
  - `BatchJobRepository.list_step_runs(job_id: int) -> list[BatchJobStepRunRecord]`
  - `recover_expired_claims`가 고아 step_run을 마감

- [ ] **Step 1: 실패하는 테스트 작성**

```python
@pytest.mark.anyio
async def test_list_step_runs_returns_records_in_seq_order():
    session = RecordingAsyncSession(
        results=[
            DummyResult(
                [
                    {
                        'step_run_id': 11,
                        'step_code': 'CREATE_JOB',
                        'seq': 1,
                        'status': 'SUCCEEDED',
                        'started_at': datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
                        'ended_at': datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
                        'duration_ms': 1000,
                    },
                    {
                        'step_run_id': 12,
                        'step_code': 'DEDUPE_ARTICLES',
                        'seq': 2,
                        'status': 'RUNNING',
                        'started_at': datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
                        'ended_at': None,
                        'duration_ms': None,
                    },
                ]
            )
        ]
    )
    repo = BatchJobRepository(session)

    records = await repo.list_step_runs(1001)

    assert [record.step_code for record in records] == [
        'CREATE_JOB',
        'DEDUPE_ARTICLES',
    ]
    assert records[0].duration_ms == 1000
    assert records[1].ended_at is None
    sql = normalize_sql(session.statements[0]).lower()
    assert 'from stock.batch_job_step_run' in sql
    assert 'order by seq' in sql
    assert session.parameters[0] == {'job_id': 1001}


@pytest.mark.anyio
async def test_recover_expired_claims_closes_orphan_step_runs():
    session = RecordingAsyncSession(
        results=[DummyResult([1]), DummyResult([2]), DummyResult([3])]
    )
    repo = BatchJobRepository(session)

    await repo.recover_expired_claims()

    orphan_sql = normalize_sql(session.statements[2]).lower()
    assert 'update stock.batch_job_step_run' in orphan_sql
    assert "sr.status = 'running'" in orphan_sql
    assert "j.status <> 'running'" in orphan_sql
```

`tests/repositories/test_batch_job_repo.py` 상단 import에 `UTC`, `datetime`이 이미 있는지 확인한다(파일 3행에 `from datetime import UTC, date, datetime`이 있다).

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v -k "list_step_runs or orphan_step_runs"`
Expected: FAIL — `list_step_runs` 미정의, 그리고 `recover_expired_claims`가 세 번째 문장을 실행하지 않아 IndexError

- [ ] **Step 3: `BatchJobStepRunRecord` 추가**

`app/db/repositories/projections.py`의 `BatchLeaseRecoveryResult` 정의 뒤에 추가하고 `__all__`이 있다면 함께 갱신:

```python
@dataclass(slots=True)
class BatchJobStepRunRecord:
    step_run_id: int
    step_code: str
    seq: int
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None
```

`datetime` import가 이미 있는지 확인하고 없으면 `from datetime import date, datetime`으로 보강한다.

- [ ] **Step 4: `list_step_runs` 구현**

`app/db/repositories/batch_job_repo.py`의 `finish_step_run` 뒤에 추가:

```python
    async def list_step_runs(self, job_id: int) -> list[BatchJobStepRunRecord]:
        statement = text(
            """
            SELECT
                id AS step_run_id,
                step_code,
                seq,
                status,
                started_at,
                ended_at,
                duration_ms
            FROM {step_run_table}
            WHERE batch_job_id = :job_id
            ORDER BY seq
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
            )
        )
        result = await self.session.execute(statement, {'job_id': job_id})
        return self._models_from_mappings(
            BatchJobStepRunRecord, result.mappings().all()
        )
```

import에 `BatchJobStepRunRecord`를 추가한다:

```python
from app.db.repositories.projections import (
    BatchJobCreateParams,
    BatchJobListResult,
    BatchJobRecord,
    BatchJobStepRunRecord,
    BatchJobSummary,
    BatchLeaseRecoveryResult,
    BatchPageSource,
)
```

- [ ] **Step 5: 고아 행 정리 추가**

`app/db/repositories/batch_job_repo.py`의 `recover_expired_claims`에서 `requeued_count`를 구한 뒤, `return BatchLeaseRecoveryResult(...)` 앞에 추가:

```python
        orphan_step_statement = text(
            """
            UPDATE {step_run_table} AS sr
            SET
                status = '{status_failed_step}',
                ended_at = now(),
                duration_ms = GREATEST(
                    (EXTRACT(EPOCH FROM (now() - sr.started_at)) * 1000)::int, 0
                ),
                updated_at = now()
            FROM {batch_job_table} AS j
            WHERE sr.batch_job_id = j.id
              AND sr.status = '{status_running_step}'
              AND j.status <> '{status_running}'
            """.format(
                step_run_table=qualify_db_identifier('batch_job_step_run'),
                batch_job_table=qualify_db_identifier('batch_job'),
                status_failed_step=BatchStepStatus.FAILED.value,
                status_running_step=BatchStepStatus.RUNNING.value,
                status_running=_STATUS_RUNNING,
            )
        )
        await self.session.execute(orphan_step_statement)
```

`STEP_DURATION_MS_EXPR`는 컬럼 별칭이 없는 `started_at`을 참조하므로 여기서는 `sr.started_at`으로 명시한 식을 그대로 쓴다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/repositories/test_batch_job_repo.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/db/repositories/projections.py app/db/repositories/batch_job_repo.py tests/repositories/test_batch_job_repo.py
git commit -m "feat: 스텝 실행 이력 조회와 고아 행 정리 추가"
```

---

### Task 5: `MarketDailyBatchOrchestrator` 연결

**Files:**
- Modify: `app/batch/orchestrators/market_daily.py:127-193`
- Test: `tests/batch/test_market_daily_orchestrator.py`

**Interfaces:**
- Consumes: `begin_step(...) -> int | None`, `finish_step_run(*, step_run_id, status)`, `BatchStepStatus`
- Produces: 없음 (오케스트레이터 내부 동작)

- [ ] **Step 1: 기존 fake repository 시그니처 갱신**

`tests/batch/test_market_daily_orchestrator.py:319`와 `:411`의 fake `begin_step`은 `bool`을 반환한다. 두 곳 모두 `int | None`을 반환하도록 바꾸고, 호출 기록과 `finish_step_run`을 추가한다. 예를 들어 `:319`의 형태가 아래와 같다면:

```python
        async def begin_step(self, *, step_code, **_kwargs):
            self.begun_steps.append(step_code)
            return True
```

다음으로 바꾼다:

```python
        async def begin_step(self, *, step_code, **_kwargs):
            self.begun_steps.append(step_code)
            self.step_run_seq += 1
            return self.step_run_seq

        async def finish_step_run(self, *, step_run_id, status):
            self.finished_step_runs.append((step_run_id, status))
            return True
```

각 fake의 `__init__`(또는 클래스 속성 선언부)에 `self.step_run_seq = 0`과 `self.finished_step_runs: list[tuple[int, str]] = []`를 추가한다. 리스 유실을 흉내내는 fake가 `False`를 반환하고 있다면 `None`으로 바꾼다.

- [ ] **Step 2: 실패하는 테스트 작성**

`tests/batch/test_market_daily_orchestrator.py` 끝에 추가한다. 파일의 기존 테스트가 쓰는 fake 구성 헬퍼를 그대로 재사용하고, 아래 두 가지를 검증한다.

```python
@pytest.mark.anyio
async def test_each_step_run_is_closed_as_succeeded_on_success():
    repository, orchestrator, lease_token = _build_successful_orchestrator()

    await orchestrator.run(job_id=1001, lease_token=lease_token)

    assert repository.finished_step_runs
    assert all(
        status == 'SUCCEEDED' for _, status in repository.finished_step_runs
    )
    assert len(repository.finished_step_runs) == len(repository.begun_steps)


@pytest.mark.anyio
async def test_failing_step_run_is_closed_as_failed():
    repository, orchestrator, lease_token = _build_orchestrator_failing_at(
        'BUILD_CLUSTERS'
    )

    with pytest.raises(Exception):
        await orchestrator.run(job_id=1001, lease_token=lease_token)

    assert repository.finished_step_runs[-1][1] == 'FAILED'
```

`_build_successful_orchestrator` / `_build_orchestrator_failing_at`는 파일에 이미 있는 조립 코드를 헬퍼로 추출해 만든다. 기존 테스트들이 각자 인라인으로 조립하고 있다면, 그중 하나를 그대로 복사해 헬퍼로 만들고 새 테스트에서 쓴다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `uv run pytest tests/batch/test_market_daily_orchestrator.py -v`
Expected: FAIL — `finished_step_runs`가 비어 있음

- [ ] **Step 4: 오케스트레이터 구현**

`app/batch/orchestrators/market_daily.py`를 아래와 같이 고친다.

`current_step_started_at` 선언 옆(61-62행 근처)에 추가:

```python
            current_step_run_id: int | None = None
```

`begin_step` 호출부(127-138행)를 교체:

```python
                    if lease_token is not None:
                        current_step_run_id = await repository.begin_step(
                            job_id=job_id,
                            lease_token=lease_token,
                            step_code=step_code,
                        )
                        if current_step_run_id is None:
                            await repository.rollback()
                            raise BatchLeaseLostError(
                                f'Lease was lost before step {step_code}.'
                            )
                        await repository.commit()
```

스텝 성공 후 `await repository.commit()`(170행) 바로 앞에 추가:

```python
                    if current_step_run_id is not None:
                        await repository.finish_step_run(
                            step_run_id=current_step_run_id,
                            status=BatchStepStatus.SUCCEEDED.value,
                        )
```

같은 블록에서 `current_step_started_at = None`(178행) 옆에 추가:

```python
                    current_step_run_id = None
```

`except Exception as exc:` 블록에서 `await _rollback_active_transaction(repository)` 직후에 추가:

```python
                if current_step_run_id is not None:
                    await repository.finish_step_run(
                        step_run_id=current_step_run_id,
                        status=BatchStepStatus.FAILED.value,
                    )
                    await repository.commit()
                    current_step_run_id = None
```

import에 `BatchStepStatus`를 추가:

```python
from app.db.enums import BatchStepStatus, EventLevel
```

주의: 예외 블록은 `lease_token is not None`일 때 곧바로 `raise`하므로(202-203행), 마감 처리는 그 `raise`보다 앞에 있어야 한다. `_rollback_active_transaction` 직후에 넣으면 이 조건을 만족한다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/batch/test_market_daily_orchestrator.py -v`
Expected: PASS

- [ ] **Step 6: 커밋**

```bash
git add app/batch/orchestrators/market_daily.py tests/batch/test_market_daily_orchestrator.py
git commit -m "feat: market_daily 오케스트레이터가 스텝 실행 이력을 마감"
```

---

### Task 6: `NaverNewsCollectionOrchestrator` 연결

**Files:**
- Modify: `app/batch/orchestrators/news_collection.py:58-69`, 완료 지점(`:266` 근처), 실패 지점(`_fail_job`, `:302`)
- Test: `tests/batch/test_news_collection_orchestrator.py`

**Interfaces:**
- Consumes: `begin_step(...) -> int | None`, `finish_step_run(*, step_run_id, status)`, `BatchStepStatus`
- Produces: 없음

- [ ] **Step 1: 기존 fake repository 시그니처 갱신**

`tests/batch/test_news_collection_orchestrator.py:61`의 fake를 Task 5와 같은 형태로 바꾼다:

```python
        async def begin_step(self, **_kwargs):
            self.step_run_seq += 1
            return self.step_run_seq

        async def finish_step_run(self, *, step_run_id, status):
            self.finished_step_runs.append((step_run_id, status))
            return True
```

해당 fake의 `__init__`에 `self.step_run_seq = 0`, `self.finished_step_runs: list[tuple[int, str]] = []`를 추가한다.

- [ ] **Step 2: 실패하는 테스트 작성**

```python
@pytest.mark.anyio
async def test_collection_step_run_is_closed_as_succeeded():
    # 파일의 기존 성공 시나리오 테스트와 동일하게 조립한다.
    job_repo, orchestrator, lease_token = _build_successful_collection()

    await orchestrator.run(job_id=3001, lease_token=lease_token)

    assert job_repo.finished_step_runs == [(1, 'SUCCEEDED')]
```

`_build_successful_collection`은 파일에 이미 있는 성공 시나리오 조립 코드를 헬퍼로 추출해 만든다.

- [ ] **Step 3: 테스트 실패 확인**

Run: `uv run pytest tests/batch/test_news_collection_orchestrator.py -v -k step_run`
Expected: FAIL — `finished_step_runs`가 `[]`

- [ ] **Step 4: 구현**

`app/batch/orchestrators/news_collection.py`에서 `begin_step` 호출부(58-68행)를 교체:

```python
            step_run_id: int | None = None
            if lease_token is not None:
                step_run_id = await job_repo.begin_step(
                    job_id=job_id,
                    lease_token=lease_token,
                    step_code='COLLECT_NAVER_NEWS',
                )
                if step_run_id is None:
                    await job_repo.rollback()
                    raise BatchLeaseLostError('News collection worker lease was lost.')
```

`mark_job_completed` 호출 직후, 마지막 `await job_repo.commit()` 앞에 추가:

```python
            if step_run_id is not None:
                await job_repo.finish_step_run(
                    step_run_id=step_run_id,
                    status=BatchStepStatus.SUCCEEDED.value,
                )
```

import에 `BatchStepStatus`를 추가한다(파일의 기존 `from app.db.enums import ...` 줄에 합친다).

- [ ] **Step 5: 실패 경로 마감**

이 오케스트레이터는 `run`을 감싸는 `try/except`가 없고, 실패를 두 갈래로 처리한다.

- `_fail_job(...)` 호출 후 `return` (자격증명 미설정 등 확정 실패)
- `raise NaverRetryableError()` (일시 장애 → 재큐잉 대상)

첫 번째 갈래를 마감한다. `_fail_job` 시그니처에 `step_run_id: int | None = None`을 추가하고, `mark_job_failed` 호출 뒤 `await job_repo.commit()` 앞에 추가:

```python
        if step_run_id is not None:
            await job_repo.finish_step_run(
                step_run_id=step_run_id,
                status=BatchStepStatus.FAILED.value,
            )
```

`run` 안의 모든 `_fail_job(...)` 호출에 `step_run_id=step_run_id`를 넘긴다. 단, `begin_step`보다 앞에서 호출되는 `_fail_job`(run 메타데이터 조회 실패 등)이 있다면 그 지점에는 넘기지 않는다.

두 번째 갈래(`NaverRetryableError`)는 잡이 `PENDING`으로 재큐잉되므로, Task 4에서 추가한 `recover_expired_claims`의 고아 행 정리가 해당 step_run을 `FAILED`로 마감한다. 여기서 별도 처리를 하지 않는다.

실패 경로 테스트를 추가한다:

```python
@pytest.mark.anyio
async def test_step_run_is_closed_as_failed_when_collection_fails():
    # 파일의 기존 자격증명 미설정 실패 시나리오와 동일하게 조립한다.
    job_repo, orchestrator, lease_token = _build_unconfigured_collection()

    await orchestrator.run(job_id=3001, lease_token=lease_token)

    assert job_repo.finished_step_runs == [(1, 'FAILED')]
```

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/batch/test_news_collection_orchestrator.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/batch/orchestrators/news_collection.py tests/batch/test_news_collection_orchestrator.py
git commit -m "feat: 뉴스 수집 오케스트레이터가 스텝 실행 이력을 마감"
```

---

### Task 7: `AiRetryOrchestrator` 연결

**Files:**
- Modify: `app/batch/ai_retry/orchestrator.py:378-392` (`_begin_step`), `:86`, `:125`, `:179`, `:214` (호출부), `:249` (except 블록)
- Test: `tests/batch/test_ai_retry_orchestrator.py`

**Interfaces:**
- Consumes: `begin_step(...) -> int | None`, `finish_step_run(*, step_run_id, status)`, `BatchStepStatus`
- Produces: 모듈 내부 헬퍼 `_begin_step(...) -> int | None`, `_finish_step(repository, *, step_run_id, status) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/batch/test_ai_retry_orchestrator.py`에 추가한다. 이 파일의 fake job repository는 `_job_repo_factory`로 주입되며, `begin_step`이 없을 수도 있다(`_begin_step`이 `getattr`로 방어한다). 이번 작업에서 fake에 두 메서드를 명시적으로 추가한다.

```python
@pytest.mark.anyio
async def test_ai_retry_closes_step_runs_as_succeeded():
    job_repo, orchestrator, lease_token = _build_successful_retry()

    await orchestrator.run(job_id=4001, lease_token=lease_token)

    assert job_repo.finished_step_runs
    assert all(
        status == 'SUCCEEDED' for _, status in job_repo.finished_step_runs
    )
    assert len(job_repo.finished_step_runs) == job_repo.step_run_seq
```

fake job repository에 추가할 메서드:

```python
        async def begin_step(self, *, job_id, lease_token, step_code):
            self.begun_steps.append(step_code)
            self.step_run_seq += 1
            return self.step_run_seq

        async def finish_step_run(self, *, step_run_id, status):
            self.finished_step_runs.append((step_run_id, status))
            return True
```

`__init__`에 `self.begun_steps: list[str] = []`, `self.step_run_seq = 0`, `self.finished_step_runs: list[tuple[int, str]] = []`를 추가한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/batch/test_ai_retry_orchestrator.py -v -k step_runs`
Expected: FAIL — `finished_step_runs`가 `[]`

- [ ] **Step 3: 헬퍼 변경**

`app/batch/ai_retry/orchestrator.py`의 `_begin_step`을 교체하고 `_finish_step`을 추가:

```python
async def _begin_step(
    repository: Any,
    *,
    job_id: int,
    lease_token: UUID | None,
    step_code: str,
) -> int | None:
    method = getattr(repository, 'begin_step', None)
    if lease_token is None or method is None:
        return None
    step_run_id = await method(
        job_id=job_id,
        lease_token=lease_token,
        step_code=step_code,
    )
    if step_run_id is None:
        raise BatchLeaseLostError(f'Lease lost before AI retry step {step_code}.')
    return step_run_id


async def _finish_step(
    repository: Any,
    *,
    step_run_id: int | None,
    status: str,
) -> None:
    method = getattr(repository, 'finish_step_run', None)
    if step_run_id is None or method is None:
        return
    await method(step_run_id=step_run_id, status=status)
```

- [ ] **Step 4: 호출부 연결**

`run` 메서드에서 네 개의 `_begin_step` 호출 결과를 지역 변수 `step_run_id`에 받고, 해당 스텝의 작업이 끝나는 지점에서 `_finish_step`을 호출한다.

`try` 블록 시작 부분(`job = await retry_repo.get_job(job_id)` 앞)에 진행 중인 step_run을 추적할 변수를 선언한다:

```python
                current_step_run_id: int | None = None
```

네 곳 모두 아래 패턴을 적용한다. 예를 들어 `:86`의 SELECT 스텝은 이렇게 바뀐다:

```python
                current_step_run_id = await _begin_step(
                    job_repo,
                    job_id=job_id,
                    lease_token=lease_token,
                    step_code=AI_RETRY_SELECT_STEP,
                )
```

그리고 해당 스텝의 작업이 끝나는 지점에서 마감한다:

```python
                await _finish_step(
                    job_repo,
                    step_run_id=current_step_run_id,
                    status=BatchStepStatus.SUCCEEDED.value,
                )
                current_step_run_id = None
                await retry_repo.commit()
```

각 스텝의 마감 위치:

- `:86` SELECT 스텝 — `await _checkpoint(...)` 뒤, `await retry_repo.commit()` 앞
- `:125` GENERATE 스텝(루프 내부) — 대상 하나의 `upsert_retry_summary`가 끝난 뒤. 루프를 돌 때마다 `_begin_step`이 새 id를 돌려주므로 `current_step_run_id`도 매번 갱신된다
- `:179` BUILD_PAGE 스텝 — 페이지 구축 후 `await retry_repo.commit()` 앞
- `:214` FINALIZE 스텝 — `add_event` 뒤, 마지막 `await retry_repo.commit()` 앞

`except Exception:` 블록(`:249`)을 교체:

```python
            except Exception:
                await retry_repo.rollback()
                if current_step_run_id is not None:
                    await _finish_step(
                        job_repo,
                        step_run_id=current_step_run_id,
                        status=BatchStepStatus.FAILED.value,
                    )
                    await retry_repo.commit()
                raise
```

import에 `BatchStepStatus`를 추가한다(기존 `from app.db.enums import ...` 줄에 합친다).

- [ ] **Step 5: 테스트 통과 확인**

Run: `uv run pytest tests/batch/test_ai_retry_orchestrator.py -v`
Expected: PASS

- [ ] **Step 6: 배치 테스트 전체 확인**

Run: `uv run pytest tests/batch tests/repositories -q`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/batch/ai_retry/orchestrator.py tests/batch/test_ai_retry_orchestrator.py
git commit -m "feat: AI 재처리 오케스트레이터가 스텝 실행 이력을 마감"
```

---

### Task 8: 응답 스키마와 조립

**Files:**
- Modify: `app/schemas/batch.py:151-188`
- Modify: `app/domains/batches/assembler.py:151-213`
- Modify: `tests/support.py:580-619` (`build_batch_job_detail_payload`)
- Test: `tests/domains/test_batch_assembler.py`

**Interfaces:**
- Consumes: Task 4의 `BatchJobStepRunRecord`
- Produces:
  - `BatchJobStepRunResponse` — 필드: `stepCode: str`, `status: str`, `startedAt: datetime | str`, `endedAt: datetime | str | None = None`, `durationMs: int | None = None`
  - `BatchJobDetailResponse.steps: list[BatchJobStepRunResponse] = []`
  - `build_batch_job_detail_payload(job, news_run=None, step_runs=None)` — 세 번째 인자는 `Sequence[Any] | None`이며 기본값 `None`은 빈 배열로 취급

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/domains/test_batch_assembler.py`에 추가:

```python
@pytest.mark.anyio
async def test_detail_payload_includes_step_durations():
    job = _sample_market_snapshot_job()
    step_runs = [
        SimpleNamespace(
            step_run_id=11,
            step_code='CREATE_JOB',
            seq=1,
            status='SUCCEEDED',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            duration_ms=1000,
        ),
        SimpleNamespace(
            step_run_id=12,
            step_code='DEDUPE_ARTICLES',
            seq=2,
            status='RUNNING',
            started_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            ended_at=None,
            duration_ms=None,
        ),
    ]

    payload = build_batch_job_detail_payload(job, None, step_runs)

    assert [step['stepCode'] for step in payload['steps']] == [
        'CREATE_JOB',
        'DEDUPE_ARTICLES',
    ]
    assert payload['steps'][0]['durationMs'] == 1000
    assert payload['steps'][1]['endedAt'] is None
    assert payload['steps'][1]['durationMs'] is None


def test_detail_payload_defaults_steps_to_empty_list():
    payload = build_batch_job_detail_payload(_sample_market_snapshot_job())

    assert payload['steps'] == []
```

`_sample_market_snapshot_job`은 파일의 기존 상세 페이로드 테스트가 쓰는 잡 객체 조립 코드를 헬퍼로 추출해 만든다. 필요한 import(`SimpleNamespace`, `UTC`, `datetime`)를 파일 상단에 보강한다.

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/domains/test_batch_assembler.py -v -k step`
Expected: FAIL — `build_batch_job_detail_payload() takes 1 to 2 positional arguments but 3 were given`

- [ ] **Step 3: 스키마 추가**

`app/schemas/batch.py`의 `BatchJobNewsCollectionDetail` 뒤에 추가:

```python
class BatchJobStepRunResponse(BaseModel):
    stepCode: str
    status: str
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationMs: int | None = None
```

`BatchJobDetailResponse`의 `newsCollection` 필드 뒤에 추가:

```python
    steps: list[BatchJobStepRunResponse] = []
```

`__all__`에 `'BatchJobStepRunResponse'`를 추가한다(알파벳 순 유지: `'BatchJobSnapshotDetail'` 다음).

- [ ] **Step 4: assembler 변경**

`app/domains/batches/assembler.py`의 import에 `BatchJobStepRunResponse`를 추가하고, `build_batch_job_detail_payload` 시그니처와 본문을 고친다:

```python
def build_batch_job_detail_payload(
    job: Any,
    news_run: Any | None = None,
    step_runs: Sequence[Any] | None = None,
) -> dict[str, Any]:
```

`payload = BatchJobDetailResponse(...)` 호출에 `steps` 인자를 추가한다:

```python
        newsCollection=news_collection,
        steps=[
            BatchJobStepRunResponse(
                stepCode=step_run.step_code,
                status=step_run.status,
                startedAt=_as_required_iso(step_run.started_at),
                endedAt=_as_iso(step_run.ended_at),
                durationMs=step_run.duration_ms,
            )
            for step_run in (step_runs or [])
        ],
    )
```

파일 상단 import에 `from collections.abc import Sequence`를 추가한다.

- [ ] **Step 5: 공용 픽스처 갱신**

`tests/support.py`의 `build_batch_job_detail_payload`(580행) 반환 딕셔너리에서 `'newsCollection': None,` 뒤에 추가:

```python
        'steps': [],
```

이 픽스처는 `tests/domains/test_batches_service.py`와 `tests/api/test_batches.py`가 응답 전체를 등가 비교하는 데 쓰이므로 반드시 함께 고쳐야 한다.

- [ ] **Step 6: 테스트 통과 확인**

Run: `uv run pytest tests/domains/test_batch_assembler.py -v`
Expected: PASS

- [ ] **Step 7: 커밋**

```bash
git add app/schemas/batch.py app/domains/batches/assembler.py tests/support.py tests/domains/test_batch_assembler.py
git commit -m "feat: 배치 상세 응답에 스텝 소요 시간 필드 추가"
```

---

### Task 9: 서비스 계층 연결

**Files:**
- Modify: `app/domains/batches/service.py:178-190`
- Test: `tests/domains/test_batches_service.py`, `tests/api/test_batches.py`

**Interfaces:**
- Consumes: Task 4의 `list_step_runs`, Task 8의 `build_batch_job_detail_payload(job, news_run, step_runs)`
- Produces: `BatchesService.get_job_detail(job_id)` 응답에 `steps` 포함

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/domains/test_batches_service.py`에 추가:

```python
@pytest.mark.anyio
async def test_get_job_detail_includes_step_runs():
    repository = FakeBatchJobRepository(
        detailed_job=BatchJobRecord(
            job_id=1001,
            job_name='market_daily_batch',
            business_date=date(2026, 8, 7),
            status='SUCCESS',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 2, tzinfo=UTC),
            duration_seconds=120,
            market_scope='GLOBAL',
            raw_news_count=0,
            processed_news_count=0,
            cluster_count=0,
            page_id=None,
            page_version_no=None,
            run_mode='FULL',
        )
    )
    repository.step_runs = [
        SimpleNamespace(
            step_run_id=11,
            step_code='CREATE_JOB',
            seq=1,
            status='SUCCEEDED',
            started_at=datetime(2026, 8, 7, 0, 0, tzinfo=UTC),
            ended_at=datetime(2026, 8, 7, 0, 0, 1, tzinfo=UTC),
            duration_ms=1000,
        )
    ]
    service = BatchesService(repository)

    result = await service.get_job_detail(1001)

    assert result['steps'] == [
        {
            'stepCode': 'CREATE_JOB',
            'status': 'SUCCEEDED',
            'startedAt': '2026-08-07T00:00:00+00:00',
            'endedAt': '2026-08-07T00:00:01+00:00',
            'durationMs': 1000,
        }
    ]
    assert repository.list_step_runs_calls == [1001]
```

`FakeBatchJobRepository`(같은 파일 25행)의 `__init__`에 추가:

```python
        self.step_runs: list = []
        self.list_step_runs_calls: list[int] = []
```

그리고 메서드를 추가:

```python
    async def list_step_runs(self, job_id):
        self.list_step_runs_calls.append(job_id)
        return self.step_runs
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `uv run pytest tests/domains/test_batches_service.py -v -k step_runs`
Expected: FAIL — `result['steps']`가 `[]`이고 `list_step_runs_calls`가 비어 있음

- [ ] **Step 3: 구현**

`app/domains/batches/service.py`의 `get_job_detail`을 교체:

```python
    async def get_job_detail(self, job_id: int) -> dict[str, object]:
        job = await self._repo.get_job_by_id(job_id)
        if job is None:
            raise NotFoundError(
                'BATCH_JOB_NOT_FOUND', '요청한 배치 작업을 찾을 수 없습니다.'
            )
        news_run = None
        if derive_batch_job_type(job.run_mode) == BatchJobType.NEWS_COLLECTION:
            run_repo = self._news_collection_repo or NewsCollectionRunRepository(
                self._repo.session
            )
            news_run = await run_repo.get_by_job_id(job_id)
        step_runs = await self._repo.list_step_runs(job_id)
        return build_batch_job_detail_payload(job, news_run, step_runs)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `uv run pytest tests/domains/test_batches_service.py -v`
Expected: PASS

- [ ] **Step 5: API 계층 테스트 확인**

`tests/api/test_batches.py`가 상세 엔드포인트를 호출할 때 쓰는 fake 서비스/리포지토리에도 `list_step_runs`가 필요할 수 있다. 실행해서 확인하고, 실패하면 위와 같은 형태로 `list_step_runs`를 추가한다.

Run: `uv run pytest tests/api/test_batches.py tests/contracts -v`
Expected: PASS

- [ ] **Step 6: 전체 테스트와 린트**

Run: `uv run pytest tests -q && uv run ruff check . && uv run ruff format --check .`
Expected: 전부 PASS

- [ ] **Step 7: 커밋**

```bash
git add app/domains/batches/service.py tests/domains/test_batches_service.py tests/api/test_batches.py
git commit -m "feat: 배치 상세 조회가 스텝 소요 시간을 함께 반환"
```

---

### Task 10: API 문서 갱신

**Files:**
- Modify: `docs/api_spec_doc.md:355-388`

**Interfaces:**
- Consumes: Task 8~9의 응답 형태
- Produces: 없음

- [ ] **Step 1: 응답 예시 갱신**

`### GET /batch/jobs/{jobId}`의 응답 예시에서 `"logSummary"` 줄 뒤에 추가:

```json
    "steps": [
      {
        "stepCode": "CREATE_JOB",
        "status": "SUCCEEDED",
        "startedAt": "2026-03-18T06:10:00+09:00",
        "endedAt": "2026-03-18T06:10:00+09:00",
        "durationMs": 12
      },
      {
        "stepCode": "DEDUPE_ARTICLES",
        "status": "SUCCEEDED",
        "startedAt": "2026-03-18T06:10:00+09:00",
        "endedAt": "2026-03-18T06:10:04+09:00",
        "durationMs": 4210
      }
    ]
```

예시 뒤에 설명 문단을 추가한다:

```markdown
`steps`는 해당 잡이 실행한 스텝을 실행 순서대로 담는다. `status`는
`RUNNING` / `SUCCEEDED` / `FAILED` 중 하나이며, 진행 중인 스텝은
`endedAt`과 `durationMs`가 `null`이다. 체크포인트 재개나 재시도로 같은
스텝이 여러 번 실행되면 항목도 여러 개 나타난다. AI 재처리 잡은 재처리
대상마다 생성 스텝 항목이 하나씩 생긴다. 이 기능 도입 이전에 실행된
잡은 빈 배열을 반환한다.
```

- [ ] **Step 2: 문서 정합성 확인**

Run: `uv run pytest tests/contracts -v`
Expected: PASS

- [ ] **Step 3: 커밋**

```bash
git add docs/api_spec_doc.md
git commit -m "docs: 배치 상세 응답의 스텝 소요 시간 필드 문서화"
```

---

## 마무리 검증

- [ ] `uv run pytest tests -q` 전체 통과
- [ ] `uv run ruff check .` / `uv run ruff format --check .` 통과
- [ ] `uv run alembic heads`가 `20260807_01_step_run` 단일 head를 출력
- [ ] 빈 DB에 `uv run alembic upgrade head`가 성공하고, `uv run alembic downgrade -1` 후 재차 `upgrade head`가 성공
