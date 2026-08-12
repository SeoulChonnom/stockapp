# B3 Hierarchical Themes and Archive Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Classify each news cluster into up to three predefined leaf themes during batch processing and expose hierarchical, correlated archive filtering/search over immutable snapshots.

**Architecture:** Seed a normalized self-referencing theme catalog, persist ranked source-cluster assignments and copied snapshot assignments, and use recursive PostgreSQL queries for descendant expansion. Start with theme codes in the existing cluster-enrichment call; evaluate it using the approved repeated-call gate. If it fails after one prompt/validation correction, delete the inline implementation and replace it with a dedicated classifier call. A deterministic precision-first keyword fallback is always available for enrichment failures.

**Tech Stack:** PostgreSQL recursive CTEs and `pg_trgm`, SQLAlchemy text queries, Gemini JSON, PyYAML, FastAPI/Pydantic, pytest, offline evaluation scripts.

---

## Task 1: Add theme schema and canonical seed test-first

**Files:**
- Create: `db/migrations/20260813_08_theme_catalog_archive_search.sql`
- Modify: `db/schema_postgresql.sql`
- Modify: `tests/db/test_schema_migrations.py`
- Modify: `tests/db/test_migrations_postgresql.py`

1. Add failing static schema tests for `pg_trgm` and these structures:

```sql
CREATE TABLE theme_catalog (
    code TEXT PRIMARY KEY,
    parent_code TEXT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    sort_order SMALLINT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (parent_code, sort_order)
);

CREATE TABLE news_cluster_theme (
    cluster_id BIGINT NOT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 3),
    classification_method TEXT NOT NULL
        CHECK (classification_method IN ('LLM', 'KEYWORD_FALLBACK')),
    classified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cluster_id, theme_code),
    UNIQUE (cluster_id, rank)
);

CREATE TABLE market_daily_page_market_cluster_theme (
    page_market_cluster_id BIGINT NOT NULL
        REFERENCES market_daily_page_market_cluster(id) ON DELETE CASCADE,
    theme_code TEXT NOT NULL REFERENCES theme_catalog(code) ON DELETE RESTRICT,
    rank SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 3),
    PRIMARY KEY (page_market_cluster_id, theme_code),
    UNIQUE (page_market_cluster_id, rank)
);
```

2. Add `search_document TEXT NOT NULL DEFAULT ''` to both `market_daily_page_market` and `market_daily_page_market_cluster`, with a `GIN (search_document gin_trgm_ops)` index on each. The market document contains its label/title/body/analysis text. The cluster document contains normalized cluster title, summary, representative title, and all same-cluster article titles copied at snapshot time.
3. Add indexes for `theme_catalog(parent_code, sort_order)`, both theme tables by `(theme_code, owning_id)`, and cluster themes by `(cluster_id, rank)`.
4. Seed exactly the approved catalog from design sections B3.2–B3.3: 5 roots, 18 intermediate nodes, and 40 leaves. Use idempotent `INSERT ... ON CONFLICT (code) DO UPDATE` so label/description/order/activity changes converge on deploy.
5. The migration must be transaction-wrapped, use qualified identifiers consistent with current migrations, and be safe when applied twice.
6. In the live PostgreSQL test, assert all counts, no cycles, every leaf depth is three, rank checks, uniqueness checks, and seed idempotency.
7. Run and observe static failures first, then:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/db/test_schema_migrations.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/db/test_migrations_postgresql.py -q
```

8. Commit:

```bash
git add db/schema_postgresql.sql db/migrations/20260813_08_theme_catalog_archive_search.sql tests/db/test_schema_migrations.py tests/db/test_migrations_postgresql.py
git commit -m "feat: 계층형 테마 스키마 추가"
```

## Task 2: Add and strictly validate keyword rules

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `app/batch/theme_rules.yaml`
- Create: `app/batch/theme_rules.py`
- Create: `tests/batch/test_theme_rules.py`

1. Add PyYAML as a direct runtime dependency with `uv add pyyaml`; it is currently only transitive and cannot be relied on as part of this app's contract.
2. Author all 40 leaf entries from the approved catalog. Each entry has `include`, `strong`, `exclude`, and `fallbackEnabled`. Mark exactly the 20 fallback candidates listed in design B3.7 as enabled.
3. Write failing loader tests for duplicate codes, missing/extra leaf codes, blank terms, overlap between include/exclude, non-list values, unknown keys, and an enabled candidate without strong evidence terms.
4. Implement a cached loader returning frozen typed rule objects. Resolve the YAML path relative to the module, not the process working directory.
5. Add a catalog-code input to the loader's validation so tests and startup/batch code fail closed when rules and the seeded catalog disagree.
6. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_theme_rules.py -q
```

7. Commit:

```bash
git add pyproject.toml uv.lock app/batch/theme_rules.yaml app/batch/theme_rules.py tests/batch/test_theme_rules.py
git commit -m "feat: 테마 분류 규칙 카탈로그 추가"
```

## Task 3: Implement the deterministic precision-first fallback

**Files:**
- Create: `app/batch/theme_classifier.py`
- Create: `tests/batch/test_theme_classifier.py`
- Create: `tests/fixtures/theme_fallback_cases.yaml`

1. Encode at least 10 positive, 5 boundary, and 10 negative labeled cases for each of the 20 enabled fallback themes. Keep the fixture human-reviewable and assign one expected primary leaf or no assignment.
2. Add failing unit tests for the approved score:
   - cluster title match: 5;
   - representative title: 4;
   - general article titles: 3 each, capped at 6;
   - summary/excerpt: 1 each, capped at 3;
   - strong term bonus: 2;
   - any exclusion match: veto.
3. Require total score at least 6 and either a strong term in a title or evidence in two distinct articles. Normalize Unicode NFC, case, and whitespace before matching.
4. Deduplicate sibling themes supported by the same evidence. Sort accepted themes by score descending, distinct-evidence count descending, catalog sort order ascending, then code ascending; return at most three.
5. Return ranked `ThemeAssignment(theme_code, rank, classification_method='KEYWORD_FALLBACK')` values and no low-confidence assignment.
6. Add a fixture evaluation test asserting precision >=95%, recall >=70%, negative false-positive rate <=5%, and byte-for-byte deterministic repeated output.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_theme_classifier.py -q
```

8. Commit:

```bash
git add app/batch/theme_classifier.py tests/batch/test_theme_classifier.py tests/fixtures/theme_fallback_cases.yaml
git commit -m "feat: 정밀도 우선 테마 fallback 추가"
```

## Task 4: Add ranked theme persistence repositories

**Files:**
- Modify: `app/db/repositories/projections.py`
- Modify: `app/db/repositories/cluster_repo.py`
- Modify: `app/db/repositories/news_cluster_write_repo.py`
- Create: `app/db/repositories/theme_repo.py`
- Modify: `tests/repositories/test_cluster_repo.py`
- Modify: `tests/repositories/test_news_cluster_write_repo.py`
- Create: `tests/repositories/test_theme_repo.py`

1. Add `ThemeAssignmentCreateParams` and read records with closed classification method typing at application boundaries.
2. Add `replace_cluster_themes(cluster_id, assignments)` that deletes old assignments and inserts the complete new ranked set in the same transaction. Validate 1–3 unique leaf codes and contiguous ranks before SQL.
3. Add cluster reads for ranked theme codes and batch reads by business date needed by snapshot construction.
4. Add `ThemeRepository.list_active_tree_rows()` and `expand_active_theme_codes(theme_codes)` using a cycle-safe recursive CTE. Expansion includes the requested code and every active descendant.
5. Add `validate_active_theme_codes` that reports the exact unknown/inactive inputs for the service to turn into `INVALID_THEME`.
6. Test empty input, root/intermediate/leaf expansion, inactive descendants, duplicates, rank constraints, and replace semantics.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_cluster_repo.py tests/repositories/test_news_cluster_write_repo.py tests/repositories/test_theme_repo.py -q
```

8. Commit:

```bash
git add app/db/repositories/projections.py app/db/repositories/cluster_repo.py app/db/repositories/news_cluster_write_repo.py app/db/repositories/theme_repo.py tests/repositories
git commit -m "feat: 클러스터 테마 저장소 추가"
```

## Task 5: Implement inline theme enrichment (candidate A)

**Files:**
- Modify: `app/batch/providers/llm_provider.py`
- Modify: `app/batch/steps/build_clusters.py`
- Modify: `app/batch/steps/cluster_enrichment.py`
- Modify: `tests/batch/test_llm_provider.py`
- Modify: `tests/batch/test_build_clusters_step.py`
- Modify: `tests/batch/test_ai_summary_normalization.py`

1. Add failing provider tests requiring `enrich_cluster` to request `themeCodes` as 1–3 unique leaf codes from the approved 40-code allowlist, in primary-first order.
2. Add a pure parser call around `themeCodes`. Invalid theme output must not invalidate valid title, summaries, tags, representative selection, or analysis paragraphs.
3. For each persisted cluster:
   - valid LLM theme codes → write `LLM` assignments;
   - missing/invalid/provider-failed theme output → run keyword fallback;
   - fallback miss → persist no assignments, add `THEME_CLASSIFICATION_MISSING` to context partial reasons;
   - rules/catalog mismatch or DB write error → raise and fail the step.
4. Ensure cluster upsert and `replace_cluster_themes` are one transactional batch-step unit and checkpoint resume replaces ranks deterministically.
5. Do not add a feature flag, a second LLM call, or candidate-B code yet.
6. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_llm_provider.py tests/batch/test_build_clusters_step.py tests/batch/test_ai_summary_normalization.py -q
```

7. Commit the candidate so evaluation has an auditable baseline:

```bash
git add app/batch/providers/llm_provider.py app/batch/steps tests/batch
git commit -m "feat: 클러스터 enrichment에 테마 분류 추가"
```

## Task 6: Run the approved A-vs-baseline evaluation gate

**Files:**
- Create: `scripts/evaluate_theme_enrichment.py`
- Create: `tests/fixtures/theme_enrichment_eval.json`
- Create: `docs/evaluations/2026-08-13-theme-enrichment.md`
- Test: `tests/batch/test_theme_enrichment_evaluation.py`

1. Build a fixed manually labeled dataset of 40 real representative clusters: KR 20 and US 20. Store source article IDs/titles/excerpts, expected primary leaf, and accepted secondary leaves; do not store credentials or full copyrighted article bodies.
2. The script invokes the baseline prompt three times and candidate-A prompt three times per cluster: 40 × 2 × 3 = 240 calls. Record per-call validity, assigned codes, latency, token usage, and raw-response hash; do not log prompt secrets.
3. Compute the approved gate exactly:
   - overall enrichment success drop <=1 percentage point;
   - invalid theme-code response <=2%;
   - fallback assignment rate >=95% among failed theme outputs;
   - manual primary-theme accuracy >=90%;
   - three-run agreement >=80%;
   - p95 latency increase <=20%;
   - average token increase <=25%.
4. Make the test validate metric calculation from a small deterministic fixture; the live script writes a Markdown/JSON result and exits nonzero when any threshold fails.
5. Run candidate A. If it fails, make exactly one prompt/validation correction, commit it as `fix: 테마 enrichment 평가 보정`, and rerun the complete 240 calls.
6. Record model name, prompt version, dataset hash, timestamps, raw metrics, pass/fail, and the final implementation decision in `docs/evaluations/2026-08-13-theme-enrichment.md`.
7. If A passes, continue to Task 8. If A still fails, execute Task 7; do not keep any A-specific `themeCodes` prompt/parser code.

## Task 7: Replace failed candidate A with dedicated classification (candidate B, conditional)

**Files:**
- Modify: `app/batch/providers/llm_provider.py`
- Modify: `app/batch/steps/build_clusters.py`
- Create: `app/batch/steps/classify_cluster_themes.py`
- Modify: `app/batch/steps/__init__.py`
- Modify: `app/batch/orchestrators/market_daily.py`
- Modify: `tests/batch/test_build_clusters_step.py`
- Create: `tests/batch/test_classify_cluster_themes_step.py`
- Modify: `tests/batch/test_market_daily_orchestrator.py`

1. First delete `themeCodes` from `enrich_cluster`, remove its parser/persistence branch, and restore the baseline enrichment tests. Confirm no A/B feature switch remains.
2. Add failing tests for `BatchLlmProvider.classify_cluster_themes` and a `CLASSIFY_CLUSTER_THEMES` step placed after `BUILD_CLUSTERS` and before `COLLECT_MARKET_INDICES`.
3. The dedicated step reads persisted clusters/articles, requests 1–3 allowed leaf codes, validates them, applies the same deterministic fallback, replaces assignments, and reports missing assignment partials.
4. Reuse `theme_classifier.py`; do not duplicate fallback logic.
5. Verify checkpoint target keys include cluster ID and classifier prompt version so resumes are idempotent.
6. Run focused tests, then commit the replacement as one coherent diff:

```bash
git add app/batch/providers/llm_provider.py app/batch/steps app/batch/orchestrators/market_daily.py tests/batch
git commit -m "refactor: 테마 분류를 독립 배치 단계로 교체"
```

7. Update the evaluation report to state candidate A was removed and candidate B is the production path.

## Task 8: Copy themes and search documents into snapshots

**Files:**
- Modify: `app/db/repositories/page_snapshot_write_repo.py`
- Modify: `app/db/repositories/page_snapshot_repo.py`
- Modify: `app/batch/steps/build_page_snapshot.py`
- Modify: `tests/repositories/test_page_snapshot_write_repo.py`
- Modify: `tests/batch/test_build_page_snapshot_rebuild.py`

1. Add write methods for ranked snapshot themes and extend `insert_page_market_cluster` to return the inserted snapshot cluster ID.
2. In `BuildPageSnapshotStep`, build each market `search_document` from label, summary title/body, background, key themes, and outlook. Group article links by source cluster before cluster insertion, then build each cluster `search_document` with the shared NFC/casefold/whitespace normalizer from title, summary, representative title, and all article titles.
3. Insert snapshot themes immediately after each snapshot cluster. If a source cluster has no theme assignment, add the explicit page issue `THEME_CLASSIFICATION_MISSING` and make the page `PARTIAL`.
4. For `rebuild_page_only`, copy source snapshot `search_document` and theme rows. Never reread mutable source-cluster assignments and never rerun classification.
5. Add snapshot tests for 1/2/3 themes, no theme, rank preservation, rebuild reproducibility, and rollback on catalog FK/write failure.
6. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_page_snapshot_write_repo.py tests/batch/test_build_page_snapshot_rebuild.py -q
```

7. Commit:

```bash
git add app/db/repositories/page_snapshot_write_repo.py app/db/repositories/page_snapshot_repo.py app/batch/steps/build_page_snapshot.py tests/repositories/test_page_snapshot_write_repo.py tests/batch/test_build_page_snapshot_rebuild.py
git commit -m "feat: 페이지 스냅샷에 테마와 검색 문서 저장"
```

## Task 9: Add the theme catalog API

**Files:**
- Modify: `app/schemas/page.py`
- Modify: `app/domains/archive/router.py`
- Modify: `app/domains/archive/service.py`
- Modify: `app/domains/archive/assembler.py`
- Create: `tests/api/test_archive_themes.py`
- Modify: `tests/domains/test_archive_service.py`

1. Add failing tests for `GET /stock/api/pages/archive/themes`. It returns active roots ordered by `sortOrder`, with recursively ordered children; `children` is always present as a list, including on leaves.
2. Define a recursive `ThemeNodeResponse` with `code`, `label`, `description`, and `children`. Keep database sort order internal unless the approved wire example explicitly exposes it.
3. Build the tree iteratively from repository rows. Detect orphan/cycle conditions as internal catalog errors rather than returning a partial tree.
4. Wire `ThemeRepository` into `ArchiveService` and register `/archive/themes` before the archive list route for clear OpenAPI ordering.
5. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/test_archive_themes.py tests/domains/test_archive_service.py -q
```

6. Commit:

```bash
git add app/schemas/page.py app/domains/archive tests/api/test_archive_themes.py tests/domains/test_archive_service.py
git commit -m "feat: 아카이브 테마 카탈로그 API 추가"
```

## Task 10: Implement correlated archive filters and search

**Files:**
- Modify: `app/domains/archive/router.py`
- Modify: `app/domains/archive/service.py`
- Modify: `app/db/repositories/page_snapshot_repo.py`
- Modify: `tests/api/test_pages.py`
- Modify: `tests/domains/test_archive_service.py`
- Modify: `tests/repositories/test_page_snapshot_repo.py`

1. Add repeated `theme` query parameters (`list[str]`, max 10), `marketType: Literal['US','KR'] | None`, and `q: str | None` with length 2–100. Reject more than 10 normalized search tokens.
2. Validate every requested theme as active before querying. Any unknown/inactive code returns HTTP 422 with `INVALID_THEME`; do not ignore part of a multi-select request.
3. Expand roots/intermediate nodes to active descendants. Multiple theme inputs are OR; date, status, market, theme, and q filters combine with AND.
4. Normalize q using NFC, casefold, collapsed spaces, and tokens. Tokens use AND semantics.
5. Implement archive SQL as `latest_public` first (from B5), then correlated `EXISTS` scopes:
   - q only: all tokens match one page/market/cluster unit;
   - market + q: selected market summary or one selected-market cluster;
   - theme + q: all tokens match one cluster carrying a selected/descendant theme;
   - market + theme + q: all predicates match that same cluster.
6. Never satisfy different q tokens using different clusters when theme is present. List and count must share one filter-builder and identical bind parameters.
7. Add repository SQL/behavior tests for parent expansion, multiple themes, each correlation scope, Unicode/spacing normalization, token cap, invalid themes, and pagination after filtering.
8. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/test_pages.py tests/domains/test_archive_service.py tests/repositories/test_page_snapshot_repo.py -q
```

9. Commit:

```bash
git add app/domains/archive app/db/repositories/page_snapshot_repo.py tests/api/test_pages.py tests/domains/test_archive_service.py tests/repositories/test_page_snapshot_repo.py
git commit -m "feat: 테마 기반 아카이브 검색 추가"
```

## Task 11: Verify B3 end to end

1. Update `tests/contracts/test_openapi_read_routes.py` for the tree response, repeated themes, market enum, q bounds, and `INVALID_THEME` response.
2. Run all B3-focused tests plus full migrations when available:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_theme_rules.py tests/batch/test_theme_classifier.py tests/batch/test_build_clusters_step.py tests/batch/test_build_page_snapshot_rebuild.py tests/repositories/test_theme_repo.py tests/repositories/test_news_cluster_write_repo.py tests/repositories/test_page_snapshot_repo.py tests/domains/test_archive_service.py tests/api/test_archive_themes.py tests/api/test_pages.py tests/contracts/test_openapi_read_routes.py tests/db/test_schema_migrations.py
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests scripts
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests scripts
git diff --check
```

3. Confirm the evaluation report names exactly one production LLM strategy. Confirm all 40 YAML leaves match all 40 active catalog leaves and all 20 fallback candidates pass their fixture gate.
4. Commit evaluation artifacts and OpenAPI assertions:

```bash
git add scripts/evaluate_theme_enrichment.py tests/fixtures/theme_enrichment_eval.json tests/batch/test_theme_enrichment_evaluation.py docs/evaluations/2026-08-13-theme-enrichment.md tests/contracts/test_openapi_read_routes.py
git commit -m "test: 테마 분류 평가 및 계약 검증"
```
