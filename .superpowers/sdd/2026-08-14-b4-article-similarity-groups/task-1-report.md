# B4 Task 1 report: article similarity group persistence

## Changes

- Added the transactional, idempotent `20260813_09_article_similarity_groups.sql`
  migration.
- Added source grouping status/generated-at/issue fields and `READY` /
  `UNAVAILABLE` consistency checks to `news_cluster`.
- Added `news_cluster_similar_group` and
  `news_cluster_similar_group_article`, including deferred representative
  membership FK, rank/check constraints, uniqueness, and lookup indexes.
- Added grouping status metadata to page snapshot clusters and nullable group
  rank, representative flag, and nonnegative exact-duplicate count to page
  article links.
- Mirrored the desired end state in `db/schema_postgresql.sql` without a
  vector extension or vector column.
- Added Alembic revision `20260814_03_article_similarity_groups` chained from
  `20260814_02_page_search_document`. The revision widens the legacy Alembic
  version column before recording its descriptive identifier.
- Added static and PostgreSQL 17 coverage for fresh, previous-head,
  stamped-existing, unversioned startup, SQL/Alembic idempotency, constraints,
  indexes, and vector absence.

## TDD and verification

- RED observed first: `tests/db/test_schema_migrations.py` reported 3 expected
  failures for the missing schema, SQL asset, and Alembic revision.
- `STOCKAPP_MIGRATION_TEST_DSN=postgresql://postgres:postgres@localhost:55432/stockapp UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/db -q`
  -> 69 passed on disposable PostgreSQL 17; the `stockapp-b4-pg17` container
  was removed afterward.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest` -> 1029 passed, 21 skipped.
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .` -> 218 files
  already formatted.
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .` -> all checks passed.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pyright app/db` -> 0 errors, 0 warnings.
- `git diff --check` -> clean.

## Residual risks

- The persistence contract is ready for later B4 repository, batch, snapshot,
  and API tasks; no runtime grouping behavior is introduced in this task.
- The live suite uses a disposable local PostgreSQL 17 instance and does not
  represent a production deployment or data-volume benchmark.
- Existing `docs/backend-requests.md` remains untouched and untracked.
