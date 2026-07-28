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

## Configuration notes

- Production startup validates that `STOCKAPP_DATABASE_URL` is not the bundled local default and that `STOCKAPP_JWT_SECRET` is a base64url secret with at least 32 decoded bytes.
- Development CORS is enabled only when `STOCKAPP_APP_ENV=development` and `STOCKAPP_CORS_ALLOWED_ORIGINS` is set. Production CORS policy is a deployment decision and should not be changed without confirming the frontend origin model.
- Naver and Gemini keys are optional at settings load time, but batch collection and LLM calls need valid provider credentials to produce live results.
- Request IDs use `X-Request-Id` by default. Unsafe incoming values are replaced with generated `req-...` IDs.
- LLM and article crawling timeouts and concurrency limits are configured with `STOCKAPP_LLM_TIMEOUT_SECONDS`, `STOCKAPP_LLM_CONCURRENCY_LIMIT`, `STOCKAPP_ARTICLE_CRAWL_TIMEOUT_SECONDS`, and `STOCKAPP_ARTICLE_CRAWL_CONCURRENCY_LIMIT`.
- Gemini calls are limited by `STOCKAPP_LLM_REQUESTS_PER_MINUTE` (default `12`). Clients on the same event loop share one limiter; each application worker or server process normally has its own event loop and therefore enforces an independent limit.

## Operations

- Health with database check: `GET /stock/api/health`
- Run tests locally: `uv run pytest`
- Run a focused test module: `uv run pytest tests/api/test_pages.py`

The current automated coverage used for remediation evidence is offline and static. It uses pytest, dependency overrides, fake sessions, and mocked providers rather than live Naver, Gemini, or production database calls unless explicitly running integration tests.

## Schema and deployment policy

- `db/schema_postgresql.sql` remains the schema source of truth. Introducing Alembic or another migration workflow needs an explicit governance decision.
- External failure notifications, such as Slack or paging, are not configured in this service yet. Choose the notification channel, recipients, and severity policy before implementation.
- Do not copy values from a real `.env` into documentation, tests, tickets, logs, or commits.
