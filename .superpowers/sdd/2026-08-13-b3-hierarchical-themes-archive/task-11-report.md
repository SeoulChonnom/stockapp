# B3 Task 11 — End-to-end contract gate report

Status: **PASS with one documented residual limitation**

Commit: `test: 테마 분류 평가 및 계약 검증` (final commit hash is reported with the handoff).

## Changes

- Extended `tests/contracts/test_openapi_read_routes.py` with the archive theme
  route/method, recursive `ThemeNodeResponse`, `ApiError` authentication and
  500 responses, repeated-theme raw `maxItems` plus the documented ten-distinct
  semantics, `marketType` (`US`/`KR`), defensive raw `q` bound (`maxLength: 1000`),
  normalized q documentation, and the `INVALID_THEME` 422 envelope.
- Made `BatchLlmProvider.classify_cluster_themes(theme_codes=...)` required.
  The provider no longer has a canonical-default bypass; the dedicated
  `CLASSIFY_CLUSTER_THEMES` step passes the active catalog allowlist explicitly.
- Corrected the evaluator comment so Candidate-A `v3` is explicitly historical,
  not current production. Updated only the evaluator-script/result/report hash
  fields in both tracked evaluation artifact sets; raw records and metrics were
  preserved.

## Strategy proof

Candidate B (`CLASSIFY_CLUSTER_THEMES`) is the only production LLM theme
strategy. `app/batch/providers/llm_provider.py` keeps the baseline
`enrich_cluster` content prompt without `themeCodes`; the only production
`themeCodes` prompt and parser are the dedicated classifier provider/step.
There is no Candidate-A production prompt/build write/parser or feature flag in
`app/`. The historical Candidate-A gate remains in
`docs/evaluations/2026-08-13-theme-enrichment.{json,md}` with decision
`CANDIDATE_B_REQUIRED` and its hash manifests validate.

## Catalog and fallback proof

- PostgreSQL live catalog: 63 total, 63 active, 40 active leaves.
- YAML/rules: 40 leaves. Set equality checks were all true:
  DB active leaves = YAML leaves = canonical active leaf codes.
- Enabled fallback candidates: exactly 20.
- Fixture: 510 cases across 20 themes — 210 positive, 100 boundary, 200
  negative; every theme has at least 10/5/10 of those labels. The per-theme
  precision/recall/negative-FP gates and deterministic repeatability test pass.
- Known false-positive integrity is preserved: the earnings/guidance rule has
  exactly the one intentional false positive asserted by the fixture gate.

## Verification evidence

| Check | Result |
| --- | --- |
| B3 focused command from Task 11 brief | **184 passed** |
| Full suite (`uv run pytest -q`) | **1020 passed, 11 skipped** |
| PostgreSQL migration test, all migration files twice | **9 passed** |
| Disposable PostgreSQL repository probe | **PASS** — parent expansion, latest-public exclusion, page/market/cluster q, theme+q same-cluster correlation, market+theme+q, multiple-theme OR, literal LIKE escaping, list/count parity |
| Ruff format check | **208 files already formatted** |
| Ruff lint | **All checks passed** |
| `git diff --check` | **PASS** |
| Pyright on changed/B3 production files | **0 errors, 0 warnings, 0 informations** |
| Evaluation artifact hash verification | **PASS** for final and retained-initial JSON/report/manifest sets |

The disposable container was `stockapp-b3-task11-pg`, bound to a random safe
localhost port. It was removed with `docker rm -f`; a subsequent container
listing confirmed no container with that name remains.

## q-only Unicode residual

The live probe confirmed a source-of-truth mismatch for page-only q search:
Python normalizes `STRASSE` to `strasse`, while PostgreSQL `LOWER('Straße')`
returns `straße`; the repository therefore does not match a page titled
`Straße` for q `STRASSE`. Market and cluster snapshot `search_document` values
already use the shared NFC/casefold/whitespace normalizer and matched in the
probe.

No locale-specific replacement or display-field mutation was made. A safe fix
is not a small Task 11 patch because `market_daily_page` has no immutable
normalized page search document. Ledger suggestion: add a page-level
`search_document` snapshot column in a dedicated migration, populate it from
the page title/headline at write time, backfill existing snapshots, and switch
the q-only page scope to that column with migration/live regression coverage.

## Remaining tool finding

`vulture app tests scripts` reports two pre-existing findings outside this
change set: `app/db/repositories/ai_retry_repo.py:9` unused `CursorResult` import
and `tests/core/test_error_envelope.py:99` unused `required` variable. No B3
Task 11 file introduced either finding.
