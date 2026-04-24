# Repository Guidelines

## Project Structure & Module Organization
`app/` contains the FastAPI application. Keep HTTP wiring in `app/api/`, shared infrastructure in `app/core/`, SQLAlchemy models and repositories in `app/db/`, response schemas in `app/schemas/`, feature logic in `app/domains/`, and batch orchestration in `app/batch/`. The runtime entrypoint is `app/main.py`; the root `main.py` re-exports `app` for tooling. Tests live under `tests/` and mirror the code layout (`tests/api/`, `tests/domains/`, `tests/repositories/`, `tests/batch/`, `tests/integration/`). Database SQL lives in `db/`, and design references live in `docs/`.

## Build, Test, and Development Commands
Use `uv` for local workflows.

- `uv sync --dev` installs app and test dependencies from `pyproject.toml` and `uv.lock`.
- `uv run fastapi dev` starts the API using the configured entrypoint in `[tool.fastapi]`.
- `uv run pytest` runs the full test suite.
- `uv run pytest tests/api/test_pages.py` runs a targeted module while iterating.

Set `UV_CACHE_DIR=/tmp/uv-cache` if the default cache path is restricted in your environment.

## Coding Style & Naming Conventions
Follow existing Python style: 4-space indentation, explicit type hints, and small modules with clear separation between routers, services, assemblers, and repositories. Use `snake_case` for functions, variables, and module names; `PascalCase` for classes; and keep FastAPI schemas focused on API contracts. No repo-wide formatter or linter is configured yet, so keep imports tidy, prefer descriptive names, and match surrounding code before introducing new patterns.

## Testing Guidelines
Tests use `pytest` with `pytest-asyncio`/AnyIO where needed. Name files `test_*.py` and keep them aligned with the production area they cover. Prefer narrowly scoped unit tests in `tests/domains/`, `tests/repositories/`, and `tests/batch/`; reserve `tests/integration/` for live or external-service flows. Reuse fixtures from `tests/conftest.py` and `tests/support.py` instead of rebuilding payload factories.

## Commit & Pull Request Guidelines
Recent history uses short conventional prefixes such as `feat:` and `fix:` followed by a concise summary, sometimes in Korean. Keep that format consistent, for example `feat: add archive query filter`. PRs should explain the user-facing change, list affected endpoints or batch steps, note any schema or env var changes, and include sample payloads or screenshots when API behavior changes.

## Security & Configuration Tips
Configuration is loaded from `.env` via `app/core/settings.py`. Do not commit real secrets; use environment variables for JWT, Naver, database, and Gemini credentials. Treat `db/schema_postgresql.sql` as the source of truth when changing persistence behavior.
