# FE Backend Contracts Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Implement the approved FE-to-BE contracts B1–B5 in the backend and then migrate `../stockfront` to the final wire contract without temporary compatibility branches.

**Architecture:** Deliver five independently testable slices in dependency order. Public page selection is corrected first; AI output contracts are persisted rather than generated at read time; hierarchical themes and similarity groups each receive their own batch persistence model; the frontend is migrated only after the backend OpenAPI contract is stable. The approved design at `docs/superpowers/specs/2026-08-13-fe-backend-contracts-design.md` remains the normative product specification.

**Tech Stack:** Python 3.14, FastAPI, Pydantic v2, SQLAlchemy text queries, PostgreSQL, pytest/AnyIO, Ollama `/api/embed`, local `bge-m3`, React 19, TypeScript 5.9, TanStack Query, Vitest, Playwright.

---

## Plan set and required order

Execute the plans in this order:

1. `2026-08-13-b5-public-page-navigation.md`
2. `2026-08-13-b1-b2-ai-response-contracts.md`
3. `2026-08-13-b3-hierarchical-themes-archive.md`
4. `2026-08-13-b4-article-similarity-groups.md`
5. `2026-08-13-stockfront-contract-integration.md`

Do not begin the frontend plan until all backend contract tests and the generated OpenAPI schema pass. B3 and B4 own separate migrations and may not share a migration file. Do not deploy between plans; the user explicitly requested a single coordinated FE/BE deployment after implementation and testing.

## Repository boundaries

- Backend repository: `/Users/ryuilkwon/SeoulChonnom/stockapp`
- Frontend repository: `/Users/ryuilkwon/SeoulChonnom/stockfront`
- Backend commands use `uv`; frontend commands use `pnpm`.
- Keep commits repository-local. Never stage `stockfront` files from `stockapp` or vice versa.
- Preserve the user's existing untracked backend file `docs/backend-requests.md`; it is outside this plan.

## Global implementation rules

1. Follow red-green-refactor for every behavior change. Run the named focused test and observe its expected failure before editing production code.
2. Keep HTTP wiring in routers, validation/orchestration in services, response construction in assemblers, and SQL in repositories.
3. Public read APIs never invoke Gemini or Ollama. They only assemble persisted batch results.
4. The public page universe is `READY` and `PARTIAL`. `FAILED` remains accessible only through explicit page-id/version and operations endpoints.
5. Snapshot rebuilds must reproduce persisted source output; they must not rerun AI, theme classification, or embedding inference.
6. Update both `db/schema_postgresql.sql` and an idempotent migration for every persistence change.
7. Do not add a feature flag or dual implementation for B3. Evaluate inline enrichment first; if it fails the approved gate after one correction, remove its A-specific code before implementing the separate classification call.
8. Never persist B4 embedding vectors. Persist only group membership, scores, status, issue, algorithm version, and generation time.
9. Error messages exposed by an API must use the existing public diagnostic sanitization path.
10. No production deployment is part of these plans.

## Shared contract invariants

- All response keys described as required remain present even when their value is `null` or an empty list.
- `processedArticleId` is a required integer in daily page links and cluster detail articles.
- Page status degradation is limited to the approved cases:
  - B1 key-point generation failure: page becomes `PARTIAL`.
  - B3 missing theme assignment for a cluster: page becomes `PARTIAL`.
  - B4 grouping failure: cluster grouping is `UNAVAILABLE`; page status is unchanged.
- Contract enums are closed in Pydantic and OpenAPI, but frontend mappers should retain a defensive unknown-state presentation at the UI boundary.
- Batch writes and snapshot copies are atomic within the owning batch step.

## Cross-plan verification checkpoints

After each backend plan:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
git diff --check
```

After B4, before frontend work:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
UV_CACHE_DIR=/tmp/uv-cache uv run vulture
git diff --check
```

If `STOCKAPP_MIGRATION_TEST_DSN` is available, also run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/db/test_migrations_postgresql.py
```

If it is unavailable, record the skip explicitly and run the schema/migration static tests; do not represent the live PostgreSQL migration as verified.

After frontend integration:

```bash
pnpm test
pnpm lint
pnpm build
pnpm knip
pnpm e2e
```

## OpenAPI synchronization checkpoint

After B1–B5 are complete, generate the backend schema from the application rather than editing a copied JSON document by hand. Compare it with `../stockfront/docs/api-spec.json`, update that file from generated output, and update `../stockfront/docs/api_spec_doc.md` for human-readable examples. The contract test in `tests/contracts/test_openapi_read_routes.py` must assert:

- `GET /stock/api/pages/navigation`
- `GET /stock/api/pages/archive/themes`
- new archive query parameters
- `DailyPageResponse.keyPoints`
- structured cluster analysis and article grouping
- required `processedArticleId`

## Final integrated acceptance gate

The work is ready for deployment review only when all of the following are true:

- Backend full test suite passes.
- Frontend full unit suite, lint, type/build, and relevant E2E suite pass.
- Both migration files are idempotent and the desired schema matches them.
- The B3 evaluation report records the selected implementation (inline A or replacement B) and all approved thresholds.
- The B4 labeled evaluation records threshold, weights, model, algorithm version, and all approved metrics.
- API examples validate against the generated OpenAPI schema.
- A smoke test against a disposable database covers a missing navigation date, latest public version selection, parent-theme expansion, structured cluster analysis, and grouping `UNAVAILABLE` fallback.
- No deployment, migration cleanup, data retirement, or temporary backward-compatibility branch has been introduced.

## Final review and commit policy

Use `superpowers:verification-before-completion` before claiming any plan complete. Review the final diff separately in each repository, then create concise conventional commits at the child-plan boundaries. Do not squash the B3 evaluation decision into an opaque commit: its A-evaluation and, only if needed, B-replacement must remain auditable.
