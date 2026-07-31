# Stockapp

FastAPI service for market daily brief collection, clustering, summarization, and read APIs.

## Requirements

- Python 3.14 or newer, as declared in `pyproject.toml`
- `uv`
- PostgreSQL with the schema from `db/schema_postgresql.sql`

## Local setup

1. Install dependencies.

   ```bash
   uv sync --dev
   ```

2. Create local configuration.

   ```bash
   cp .env.example .env
   ```

3. Fill `.env` with local values. Keep real credentials out of git. The app reads `STOCKAPP_` variables from `.env` through `app/core/settings.py`.

4. Apply the database schema from `db/schema_postgresql.sql`. Treat that file as the current schema source of truth. Use a PostgreSQL client or deployment process appropriate for your environment.

5. Start the API.

   ```bash
   uv run fastapi dev
   ```

   The FastAPI entrypoint is configured as `app.main:app` in `pyproject.toml`.

6. Enqueue a batch through the API.

   `POST /stock/api/batch/market-daily` persists a durable `PENDING` job and
   returns HTTP 202. After the response is prepared, FastAPI `BackgroundTasks`
   drains the PostgreSQL queue in the same API process. Queue claims still use
   `FOR UPDATE SKIP LOCKED`, leases, heartbeats, retries, and checkpoints, so
   concurrent requests and idempotent replays do not execute one job twice.

   The container enables a one-shot startup recovery drain. It recovers jobs
   left `PENDING`, and expired `RUNNING` leases, after a process restart. If a
   restarted process finds a still-valid `RUNNING` lease, the finite drain waits
   until its next lease deadline and then recovers it if it was not renewed.
   Local non-container runs enable the same behavior with
   `STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED=true`.

## Configuration notes

- Production startup validates that `STOCKAPP_DATABASE_URL` is not the bundled local default and that `STOCKAPP_JWT_SECRET` is a base64url secret with at least 32 decoded bytes.
- Development CORS is enabled only when `STOCKAPP_APP_ENV=development` and `STOCKAPP_CORS_ALLOWED_ORIGINS` is set. Production CORS policy is a deployment decision and should not be changed without confirming the frontend origin model.
- Naver and Gemini keys are optional at settings load time, but batch collection and LLM calls need valid provider credentials to produce live results.
- Naver incremental collection uses a 30-minute slot plus
  `STOCKAPP_NAVER_NEWS_COLLECTION_OVERLAP_MINUTES` (default `10`) for safe
  query overlap. Explicit slot replay is limited by
  `STOCKAPP_NAVER_NEWS_COLLECTION_BACKFILL_MAX_DAYS` (default `7`, maximum
  `30`).
- Request IDs use `X-Request-Id` by default. Unsafe incoming values are replaced with generated `req-...` IDs.
- LLM and article crawling timeouts and concurrency limits are configured with `STOCKAPP_LLM_TIMEOUT_SECONDS`, `STOCKAPP_LLM_CONCURRENCY_LIMIT`, `STOCKAPP_ARTICLE_CRAWL_TIMEOUT_SECONDS`, and `STOCKAPP_ARTICLE_CRAWL_CONCURRENCY_LIMIT`.
- Gemini calls are limited by both `STOCKAPP_LLM_REQUESTS_PER_MINUTE`
  (default `12`) and `STOCKAPP_LLM_TOKENS_PER_MINUTE` (default `250000`).
  Limiters are shared per `STOCKAPP_LLM_QUOTA_PROJECT_ID` and model within one
  event loop. Quota and transient failures are persisted as delayed batch
  retries with exponential backoff and jitter; provider `Retry-After`/RetryInfo
  delays take precedence. `STOCKAPP_LLM_MAX_RETRIES` bounds those durable
  retries (also capped by `STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS`), and the final
  permitted attempt keeps the deterministic fallback behavior.
- Durable worker timing is configured with `STOCKAPP_BATCH_WORKER_POLL_INTERVAL_SECONDS`, `STOCKAPP_BATCH_WORKER_HEARTBEAT_SECONDS`, `STOCKAPP_BATCH_WORKER_LEASE_SECONDS`, `STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS`, and `STOCKAPP_BATCH_WORKER_RETRY_DELAY_SECONDS`. The lease must be longer than the heartbeat interval.
- Clustering uses deterministic title-token groups and persists/enriches at most `STOCKAPP_BATCH_MAX_CLUSTERS_PER_MARKET` candidates per market (default `12`). Candidates are ranked by article count, latest publication time, and a stable article-ID tie-break before any cluster LLM calls.
- `STOCKAPP_BATCH_STARTUP_RECOVERY_ENABLED` controls the one-shot queue drain at API startup. The Docker image enables it by default; `.env.example` enables it for the documented local workflow.
- Completed XNYS/XKRX sessions are calculated with `exchange-calendars`. `STOCKAPP_MARKET_SESSION_DATA_GRACE_MINUTES` (default `30`) delays session eligibility after the regular close so yfinance has time to publish settled data.

## Market date and news coverage policy

- `business_date` is the KST page publication date. It is not required to match either market's trading session.
- Each job persists one `batch_job_market_context` row for `US` and `KR`, including the expected completed session, regular close, actual index source date, and half-open news window `[news_window_start_at, news_window_end_at)`.
- News ingestion is separate from the market-daily job. A cron caller should
  invoke `POST /stock/api/batch/news-collection` every 30 minutes; the default
  request collects the latest completed half-hour slot. An optional
  `{"slotEndAt":"2026-07-31T10:00:00+09:00"}` body replays an aligned,
  completed slot inside the configured backfill window.
- The query starts before the slot by the configured overlap, while persisted
  articles are still filtered to the half-open slot. Provider-link identity,
  raw-keyword relationships, and database constraints make overlap and replay
  idempotent across KR and US keywords.
- Naver HTTP 429 and 5xx responses release the durable job for delayed retry.
  HTTP 401/403 are terminal credential failures. Keyword failures or the Naver
  1,000-result pagination cap keep coverage incomplete.
- A retry reuses the current job context. A force run for the same `business_date` reuses the original persisted window rather than shifting its cutoff.
- Index data is normal only when `source_date == expected_session_date`. Older data and missing tickers make the job `PARTIAL`; a future source date is rejected.

## Operations

- Health with database check: `GET /stock/api/health`
- Enqueue a batch: `POST /stock/api/batch/market-daily`. Supplying a stable
  `Idempotency-Key` header makes cron retries return the original job instead of
  creating a duplicate.
- Enqueue the latest completed Naver slot:
  `POST /stock/api/batch/news-collection`. With 10 configured keywords and 48
  slots per day, normal single-page collection uses 480 of the 25,000 daily
  search calls; the theoretical 10-page-per-keyword ceiling is 4,800 calls.
- Keep API replica count at one while using the free Gemini tier. The Gemini RPM
  limiter is process-local, so multiple API replicas multiply the effective
  request rate even though PostgreSQL prevents duplicate job claims.
- Enqueue unresolved AI summary retry (ADMIN):
  `POST /stock/api/batch/jobs/{jobId}/retry-ai`
- Run tests locally: `uv run pytest`
- Run a focused test module: `uv run pytest tests/api/test_pages.py`

The current automated coverage used for remediation evidence is offline and static. It uses pytest, dependency overrides, fake sessions, and mocked providers rather than live Naver, Gemini, or production database calls unless explicitly running integration tests.

AI retry requests are durable `PENDING` jobs. Their HTTP 202 responses schedule
the same queue drain through FastAPI `BackgroundTasks`, and `runMode=AI_RETRY`
dispatches to `AiRetryOrchestrator`. See `docs/ai_summary_retry.md` for
idempotency, recovery, and page-version rules.

## Schema and deployment policy

- `db/schema_postgresql.sql` remains the schema source of truth. Introducing Alembic or another migration workflow needs an explicit governance decision.
- Existing deployments apply `db/migrations/20260729_04_batch_job_durable_queue.sql`
  and `db/migrations/20260731_07_incremental_news_collection.sql` before
  starting the updated API process.
- Apply durable queue migrations before starting the API process. Startup
  recovery and request-triggered drains use the same queue schema.
- Before switching execution modes, stop any separately deployed durable worker
  so only the API container drains the queue. The startup recovery drain
  reclaims expired `RUNNING` leases and resumes their persisted checkpoints.
- Existing databases must apply `db/migrations/20260729_05_market_session_context_source_date.sql` after the preceding numbered migrations. Existing snapshot fields remain nullable for legacy page compatibility; full new batch writes populate them.
- External failure notifications, such as Slack or paging, are not configured in this service yet. Choose the notification channel, recipients, and severity policy before implementation.
- Do not copy values from a real `.env` into documentation, tests, tickets, logs, or commits.

## Single-container execution constraints

- Run one API container with one Uvicorn worker. PostgreSQL claim fencing makes
  accidental concurrency safe, but each process has an independent Gemini rate
  limiter and in-process scheduler.
- FastAPI `BackgroundTasks` do not survive process termination. Job state,
  leases, and checkpoints do survive; the next container startup recovery drain
  resumes work after the expired lease is recoverable.
- Request background callbacks only start a managed drain and return. Provider
  execution and delayed retries run in a detached task owned by the application
  lifespan, so a future `available_at` does not keep the request task occupied.
