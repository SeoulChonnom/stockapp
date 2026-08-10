# Batch Step Error Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist a safe error summary and masked full traceback for every failed batch step execution and expose both values through `steps[]` in the batch detail API.

**Architecture:** Add two nullable columns to `batch_job_step_run`, generate diagnostics before repository writes, and keep the repository responsible only for persistence. A focused core utility formats chained tracebacks, masks configured secrets and common credential patterns, and caps output at 32 KiB; the assembler applies the masker again before returning API data.

**Tech Stack:** Python 3.14, FastAPI, Pydantic, SQLAlchemy text queries, PostgreSQL, Alembic, pytest.

## Global Constraints

- Persist only masked logs; never persist the unmasked traceback.
- DB columns use `error_message` and `error_log`; API fields use `errorMessage` and `errorLog`.
- `error_message` is a short public-safe diagnostic; `error_log` contains the masked chained traceback.
- Successful and running step rows return `NULL` for both error fields.
- Limit persisted/API error logs to 32 KiB while retaining both the beginning and final exception section.
- Do not add `attempt_no`, change the batch list API, or backfill historical rows.
- Match existing repository/orchestrator structure and avoid unrelated refactoring.

---

### Task 1: Masked exception diagnostic utility

**Files:**
- Create: `app/core/error_diagnostics.py`
- Create: `tests/core/test_error_diagnostics.py`

**Interfaces:**
- Consumes: `Settings` from `app.core.settings` and `sanitize_public_diagnostic` from `app.core.public_diagnostics`.
- Produces: `StepErrorDiagnostics(error_message: str, error_log: str)`, `build_step_error_diagnostics(exception: BaseException, *, public_message: str) -> StepErrorDiagnostics`, and `mask_error_log(value: str | None) -> str | None`.

- [ ] **Step 1: Write failing tests for safe message generation and traceback preservation**

```python
def test_build_step_error_diagnostics_preserves_frames_and_safe_message(monkeypatch):
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    try:
        raise RuntimeError('ordinary failure')
    except RuntimeError as exc:
        result = build_step_error_diagnostics(
            exc,
            public_message='배치 단계 실행 중 오류가 발생했습니다.',
        )

    assert result.error_message == '배치 단계 실행 중 오류가 발생했습니다.'
    assert 'RuntimeError: ordinary failure' in result.error_log
    assert 'test_build_step_error_diagnostics' in result.error_log
```

- [ ] **Step 2: Write failing parameterized tests for exact-value and pattern masking**

Cover configured DB credentials, JWT secret, auth stub token, Naver secret and Gemini key plus Bearer/Basic headers, credential key-value pairs, URL userinfo, JSON values and JWT-shaped strings. Assert each secret is absent and `[REDACTED]` is present.

```python
@pytest.mark.parametrize(
    'raw,secret',
    [
        ('Authorization: Bearer provider-token', 'provider-token'),
        ('{"api_key": "provider-key"}', 'provider-key'),
        ('postgresql://user:db-pass@example/db', 'db-pass'),
        ('token=temporary-token', 'temporary-token'),
    ],
)
def test_mask_error_log_redacts_common_credentials(raw, secret, monkeypatch):
    monkeypatch.setattr(error_diagnostics, 'get_settings', lambda: _settings())
    masked = mask_error_log(raw)
    assert secret not in masked
    assert '[REDACTED]' in masked
```

- [ ] **Step 3: Write failing tests for exception chaining, null input and 32 KiB truncation**

Assert `raise OuterError(...) from InnerError(...)` retains both exception classes after masking, `None` stays `None`, and oversized output contains a truncation marker with content from both ends and is no longer than 32 KiB.

- [ ] **Step 4: Run the new tests and verify they fail**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/core/test_error_diagnostics.py`

Expected: FAIL because `app.core.error_diagnostics` does not exist.

- [ ] **Step 5: Implement the minimal formatter and masker**

Use `traceback.format_exception(exception, chain=True)` without local-variable capture. Build exact sensitive values from non-empty settings fields and parsed DB URL credentials, skipping values shorter than four characters. Apply compiled case-insensitive regexes for credentials and JWTs, then cap output.

```python
MAX_ERROR_LOG_CHARS = 32 * 1024
REDACTED = '[REDACTED]'

@dataclass(frozen=True, slots=True)
class StepErrorDiagnostics:
    error_message: str
    error_log: str

def build_step_error_diagnostics(
    exception: BaseException,
    *,
    public_message: str,
) -> StepErrorDiagnostics:
    safe_message = sanitize_public_diagnostic(public_message) or 'Batch step failed.'
    raw_traceback = ''.join(traceback.format_exception(exception, chain=True))
    return StepErrorDiagnostics(safe_message, mask_error_log(raw_traceback) or '')
```

- [ ] **Step 6: Run the utility tests and existing public diagnostic tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/core/test_error_diagnostics.py tests/core/test_public_diagnostics.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 1**

```bash
git add app/core/error_diagnostics.py tests/core/test_error_diagnostics.py
git commit -m "feat: add masked batch error diagnostics"
```

### Task 2: Persist step error fields

**Files:**
- Create: `alembic/versions/20260810_01_batch_step_error_diagnostics.py`
- Modify: `db/schema_postgresql.sql:186`
- Modify: `app/db/repositories/projections.py:203`
- Modify: `app/db/repositories/batch_job_repo.py:457`
- Modify: `tests/repositories/test_batch_job_repo.py:589`

**Interfaces:**
- Consumes: already-masked `error_message` and `error_log` strings from callers.
- Produces: `finish_step_run(*, step_run_id: int, status: str, error_message: str | None = None, error_log: str | None = None) -> bool` and `BatchJobStepRunRecord.error_message/error_log`.

- [ ] **Step 1: Extend repository tests with failing assertions**

Update the failed-finish test to pass both diagnostics and assert SQL parameters include them. Update list tests so returned records include both values. Add an assertion that orphan recovery sets a fixed safe message and log.

```python
finished = await repo.finish_step_run(
    step_run_id=777,
    status='FAILED',
    error_message='External provider request failed.',
    error_log='Traceback ... [REDACTED]',
)
assert session.parameters[0]['error_message'] == 'External provider request failed.'
assert session.parameters[0]['error_log'] == 'Traceback ... [REDACTED]'
```

- [ ] **Step 2: Run targeted repository tests and verify failure**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/repositories/test_batch_job_repo.py -k "step_run or expired_claims"`

Expected: FAIL because the signature, projection and SELECT do not contain the new fields.

- [ ] **Step 3: Add the Alembic migration and canonical schema columns**

Set `revision = '20260810_01_step_errors'` and `down_revision = '20260807_01_step_run'`. Upgrade adds the two nullable `TEXT` columns; downgrade drops `error_log` and `error_message`. Mirror the final table definition in `db/schema_postgresql.sql`.

- [ ] **Step 4: Extend projection, write query and read query**

Add nullable fields to `BatchJobStepRunRecord`. Extend `finish_step_run` and set both columns, forcing them to `NULL` unless `status == BatchStepStatus.FAILED.value`. Include both columns in `list_step_runs`.

- [ ] **Step 5: Record a fixed diagnostic during orphan recovery**

When recovery closes a running step without an exception object, set:

```text
error_message = 'Batch worker stopped before the step completed.'
error_log = 'Batch step was closed during expired worker lease recovery.'
```

- [ ] **Step 6: Run repository and migration tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/repositories/test_batch_job_repo.py tests/db/test_migrations_postgresql.py tests/db/test_schema_migrations.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

```bash
git add alembic/versions/20260810_01_batch_step_error_diagnostics.py db/schema_postgresql.sql app/db/repositories/projections.py app/db/repositories/batch_job_repo.py tests/repositories/test_batch_job_repo.py
git commit -m "feat: persist batch step error diagnostics"
```

### Task 3: Capture diagnostics in every orchestrator failure path

**Files:**
- Modify: `app/batch/orchestrators/market_daily.py:192`
- Modify: `app/batch/orchestrators/news_collection.py:319`
- Modify: `app/batch/ai_retry/orchestrator.py:271`
- Modify: `tests/batch/test_market_daily_orchestrator.py`
- Modify: `tests/batch/test_news_collection_orchestrator.py`
- Modify: `tests/batch/test_ai_retry_orchestrator.py`

**Interfaces:**
- Consumes: `build_step_error_diagnostics(exception, public_message=...)` and the extended `finish_step_run` method.
- Produces: a failed step row containing a safe summary and masked traceback for each caught exception.

- [ ] **Step 1: Update fake repository signatures and write failing failure-path assertions**

Make test fakes accept `error_message=None, error_log=None`, record them, and assert failed steps receive a non-empty public message and a log containing the exception class but not injected test secrets.

```python
assert finished_step['status'] == 'FAILED'
assert finished_step['error_message'] == '배치 단계 실행 중 오류가 발생했습니다.'
assert 'RuntimeError' in finished_step['error_log']
assert 'secret-token' not in finished_step['error_log']
assert '[REDACTED]' in finished_step['error_log']
```

- [ ] **Step 2: Run the three orchestrator failure tests and verify failure**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/batch/test_market_daily_orchestrator.py tests/batch/test_news_collection_orchestrator.py tests/batch/test_ai_retry_orchestrator.py`

Expected: FAIL because failure calls do not pass diagnostic fields.

- [ ] **Step 3: Add diagnostics to Market Daily failures**

In the exception handler, build diagnostics before closing `current_step_run_id`. Use `BatchPipelineError.error_message` when present and the existing generic Korean message otherwise. Pass both fields to `finish_step_run` before committing and re-raising worker executions.

- [ ] **Step 4: Add diagnostics to News Collection failures**

For caught exceptions, use the generic public message `뉴스 수집 단계 실행 중 오류가 발생했습니다.` and the masked traceback. For `_fail_job` branches that have no exception object, pass the already-public `error_message` and a fixed structured diagnostic string.

- [ ] **Step 5: Add diagnostics to AI Retry failures**

Build diagnostics in the outer exception handler with public message `AI 재처리 단계 실행 중 오류가 발생했습니다.` and pass them through `_finish_step`; extend `_finish_step` to accept and forward both optional fields.

- [ ] **Step 6: Run all batch orchestrator tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/batch/test_market_daily_orchestrator.py tests/batch/test_news_collection_orchestrator.py tests/batch/test_ai_retry_orchestrator.py tests/batch/test_pipeline_failure_diagnostics.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 3**

```bash
git add app/batch/orchestrators/market_daily.py app/batch/orchestrators/news_collection.py app/batch/ai_retry/orchestrator.py tests/batch/test_market_daily_orchestrator.py tests/batch/test_news_collection_orchestrator.py tests/batch/test_ai_retry_orchestrator.py
git commit -m "feat: capture failed batch step tracebacks"
```

### Task 4: Expose diagnostics in the batch detail API

**Files:**
- Modify: `app/schemas/batch.py:151`
- Modify: `app/domains/batches/assembler.py:215`
- Modify: `tests/domains/test_batch_assembler.py:91`
- Modify: `tests/domains/test_batches_service.py:1029`
- Modify: `tests/api/test_batches.py`
- Modify: `docs/api_spec_doc.md`

**Interfaces:**
- Consumes: `BatchJobStepRunRecord.error_message/error_log` and `mask_error_log`.
- Produces: nullable `steps[].errorMessage` and `steps[].errorLog` fields in `BatchJobDetailResponse`.

- [ ] **Step 1: Add failing assembler and API schema assertions**

Create one failed step fixture with diagnostics and one successful step with null diagnostics. Assert the failed item returns both camel-case fields, the successful item returns `null`, and a deliberately injected Bearer token is re-masked before response validation.

- [ ] **Step 2: Run targeted domain/API tests and verify failure**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/domains/test_batch_assembler.py tests/domains/test_batches_service.py tests/api/test_batches.py`

Expected: FAIL because the response model and assembler omit both fields.

- [ ] **Step 3: Extend response schema and assembler**

```python
class BatchJobStepRunResponse(BaseModel):
    stepCode: str
    status: str
    startedAt: datetime | str
    endedAt: datetime | str | None = None
    durationMs: int | None = None
    errorMessage: str | None = None
    errorLog: str | None = None
```

Map `errorMessage` through `sanitize_public_diagnostic` and `errorLog` through `mask_error_log` in `build_batch_job_detail_payload`.

- [ ] **Step 4: Update API documentation and example payload**

Document both nullable step fields, their masked/public semantics, and a failed-step example in `docs/api_spec_doc.md`. Do not change the list endpoint contract.

- [ ] **Step 5: Run targeted response tests**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/domains/test_batch_assembler.py tests/domains/test_batches_service.py tests/api/test_batches.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 4**

```bash
git add app/schemas/batch.py app/domains/batches/assembler.py tests/domains/test_batch_assembler.py tests/domains/test_batches_service.py tests/api/test_batches.py docs/api_spec_doc.md
git commit -m "feat: expose batch step error diagnostics"
```

### Task 5: Full verification

**Files:**
- Verify only; modify a task-owned file only if a test exposes a defect in this feature.

**Interfaces:**
- Consumes: all previous task outputs.
- Produces: verified migration, repository, orchestration and API behavior.

- [ ] **Step 1: Run formatting-independent static checks**

Run: `git diff --check HEAD~4..HEAD`

Expected: no whitespace errors.

- [ ] **Step 2: Run the full test suite**

Run: `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q`

Expected: PASS.

- [ ] **Step 3: Verify Alembic graph and migration upgrade SQL**

Run: `uv run alembic heads`

Expected: exactly `20260810_01_step_errors (head)`.

- [ ] **Step 4: Inspect the final diff for scope and secret safety**

Run: `git status --short && git diff HEAD~4 --stat && rg -n "traceback\.format_exception|error_log|errorLog|REDACTED" app alembic db tests docs/api_spec_doc.md`

Expected: only planned files changed, no hard-coded real credentials, and every persistence/API path uses the masker.

- [ ] **Step 5: Record verification outcome**

If verification required no code changes, do not create an empty commit. Report the exact passing test count and Alembic head in the handoff.
