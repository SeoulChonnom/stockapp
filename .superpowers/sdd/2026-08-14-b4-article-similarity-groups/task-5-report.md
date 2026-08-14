# B4 Task 5 report: article similarity group repositories

## Implemented

- Added immutable typed projections and repository read/write paths for stored
  article similarity groups and raw-only exact duplicate counts.
- Replaced groups atomically inside the caller's transaction, locking the
  parent cluster and its ordered memberships before validation and writes.
- Inserted group headers one at a time with `RETURNING` so each persisted
  database ID is deterministic before member rows are inserted; no repository
  method commits.
- Enforced exact membership/count coverage, duplicate-ID rejection, strict
  nonnegative integer counts, finite `[0, 1]` scores, nonblank algorithm
  versions, deterministic singleton fallback rows, and schema-compatible
  `READY`/`UNAVAILABLE` status metadata.
- Correlated cluster article reads through both the similar-group member and
  group cluster IDs to prevent cross-cluster leakage.

## TDD and verification

- Review RED coverage added first for multi-group `RETURNING`, lock order,
  count coverage/types, algorithm version, score bounds, and cross-cluster
  joins.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories -q` -> **136
  passed**.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch tests/domains -q` ->
  **719 passed**, 2 dependency warnings.
- Ruff format/check and Pyright for changed repository modules -> clean.
- `git diff --check` -> clean.

No live database suite was needed for this review-only repository correction;
the existing disposable PostgreSQL 17 migration evidence is recorded in Task
1. Existing `docs/backend-requests.md` remains untouched and untracked.
