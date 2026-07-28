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

6. Start the durable batch worker in a separate process.

   ```bash
   uv run python -m app.batch.worker
   ```

   `POST /stock/api/batch/market-daily` only persists a `PENDING` job and returns
   HTTP 202. The worker atomically claims it with PostgreSQL `FOR UPDATE SKIP
   LOCKED`, renews a lease while it runs, and resumes from the last committed
   step checkpoint after a crash.

## Configuration notes

- Production startup validates that `STOCKAPP_DATABASE_URL` is not the bundled local default and that `STOCKAPP_JWT_SECRET` is a base64url secret with at least 32 decoded bytes.
- Development CORS is enabled only when `STOCKAPP_APP_ENV=development` and `STOCKAPP_CORS_ALLOWED_ORIGINS` is set. Production CORS policy is a deployment decision and should not be changed without confirming the frontend origin model.
- Naver and Gemini keys are optional at settings load time, but batch collection and LLM calls need valid provider credentials to produce live results.
- Request IDs use `X-Request-Id` by default. Unsafe incoming values are replaced with generated `req-...` IDs.
- LLM and article crawling timeouts and concurrency limits are configured with `STOCKAPP_LLM_TIMEOUT_SECONDS`, `STOCKAPP_LLM_CONCURRENCY_LIMIT`, `STOCKAPP_ARTICLE_CRAWL_TIMEOUT_SECONDS`, and `STOCKAPP_ARTICLE_CRAWL_CONCURRENCY_LIMIT`.
- Gemini calls are limited by `STOCKAPP_LLM_REQUESTS_PER_MINUTE` (default `12`). Clients on the same event loop share one limiter; each application worker or server process normally has its own event loop and therefore enforces an independent limit.
- Durable worker timing is configured with `STOCKAPP_BATCH_WORKER_POLL_INTERVAL_SECONDS`, `STOCKAPP_BATCH_WORKER_HEARTBEAT_SECONDS`, `STOCKAPP_BATCH_WORKER_LEASE_SECONDS`, `STOCKAPP_BATCH_WORKER_MAX_ATTEMPTS`, and `STOCKAPP_BATCH_WORKER_RETRY_DELAY_SECONDS`. The lease must be longer than the heartbeat interval.
- Completed XNYS/XKRX sessions are calculated with `exchange-calendars`. `STOCKAPP_MARKET_SESSION_DATA_GRACE_MINUTES` (default `30`) delays session eligibility after the regular close so yfinance has time to publish settled data.

## Market date and news coverage policy

- `business_date` is the KST page publication date. It is not required to match either market's trading session.
- Each job persists one `batch_job_market_context` row for `US` and `KR`, including the expected completed session, regular close, actual index source date, and half-open news window `[news_window_start_at, news_window_end_at)`.
- A first news run covers 24 hours. Later runs start from the most recent `news_coverage_complete=true` window end. Keyword failures or the Naver 1,000-result cap keep coverage incomplete, so the watermark does not advance.
- A retry reuses the current job context. A force run for the same `business_date` reuses the original persisted window rather than shifting its cutoff.
- Index data is normal only when `source_date == expected_session_date`. Older data and missing tickers make the job `PARTIAL`; a future source date is rejected.

## Operations

- Health with database check: `GET /stock/api/health`
- Enqueue a batch: `POST /stock/api/batch/market-daily`. Supplying a stable
  `Idempotency-Key` header makes cron retries return the original job instead of
  creating a duplicate.
- Run exactly one durable worker replica while using the free Gemini tier. The
  Gemini RPM limiter is process-local, so multiple worker replicas multiply the
  effective request rate.
- Enqueue unresolved AI summary retry (ADMIN):
  `POST /stock/api/batch/jobs/{jobId}/retry-ai`
- Run tests locally: `uv run pytest`
- Run a focused test module: `uv run pytest tests/api/test_pages.py`

The current automated coverage used for remediation evidence is offline and static. It uses pytest, dependency overrides, fake sessions, and mocked providers rather than live Naver, Gemini, or production database calls unless explicitly running integration tests.

AI retry requests are durable `PENDING` jobs and are not run in FastAPI
`BackgroundTasks`. A durable worker must dispatch `runMode=AI_RETRY` to
`AiRetryOrchestrator`. See `docs/ai_summary_retry.md` for idempotency, recovery,
and page-version rules.

## Schema and deployment policy

- `db/schema_postgresql.sql` remains the schema source of truth. Introducing Alembic or another migration workflow needs an explicit governance decision.
- Existing deployments apply `db/migrations/20260729_04_batch_job_durable_queue.sql`
  before starting the new worker. API deployment and worker deployment should
  use the same schema version.
- During the first durable-worker rollout, stop old API instances and allow any
  in-process `BackgroundTasks` batch to finish before starting the worker.
  Lease-less legacy `RUNNING` rows are intentionally recovered by the worker.
- Existing databases must apply `db/migrations/20260729_05_market_session_context_source_date.sql` after the preceding numbered migrations. Existing snapshot fields remain nullable for legacy page compatibility; full new batch writes populate them.
- External failure notifications, such as Slack or paging, are not configured in this service yet. Choose the notification channel, recipients, and severity policy before implementation.
- Do not copy values from a real `.env` into documentation, tests, tickets, logs, or commits.
