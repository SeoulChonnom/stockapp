# Stockfront Backend Contract Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Migrate `../stockfront` to the final B1–B5 backend contract, replacing current inferred/text-only behavior with persisted key points, structured analysis, hierarchical themes, article groups, and direct navigation.

**Architecture:** Update the API schema/types first, then adapters/view models, then focused UI components and route state. Keep the API DTO boundary strict and the display mapper defensive. Do not retain the old `summary.analysis[]` renderer, ±90-day archive navigation workaround, or FAILED public archive filtering after migration.

**Tech Stack:** React 19, TypeScript 5.9, TanStack Query, Vitest/Testing Library, Playwright, Biome.

---

## Preconditions

- Complete and verify all backend plans first.
- Work in `/Users/ryuilkwon/SeoulChonnom/stockfront`.
- Confirm the frontend worktree before editing; preserve unrelated user changes.
- Use the generated backend OpenAPI output as source of truth. Do not hand-invent optional fields to accommodate old payloads.

## Task 1: Synchronize API documentation and strict DTO types

**Files:**
- Modify: `docs/api-spec.json`
- Modify: `docs/api_spec_doc.md`
- Modify: `src/lib/api/types.ts`
- Modify: `src/lib/api/pages.test.ts`

1. Export the OpenAPI schema from the verified backend application and replace `docs/api-spec.json` with that generated result. Update the human-readable doc examples for navigation, themes, archive filters, key points, structured analysis, and grouping.
2. Add compile-time DTO shapes matching the backend exactly:
   - `DailyPageResponse.keyPoints`, `issues`, `navigation`, `versions`, and `metadata.isLatest`;
   - `PageDateNavigationResponse`;
   - recursive `ThemeNodeResponse`;
   - archive params `marketType`, repeated `theme`, and `q`;
   - structured cluster summary status/issues/sections/conflicts;
   - article grouping and required article group fields;
   - required numeric `processedArticleId`.
3. Remove `ClusterDetailResponse.summary.analysis: string[]` and the optional/null form of `processedArticleId` from affected DTOs.
4. Keep wire enums as string unions. UI mappers may defend against malformed runtime values, but DTO definitions must not widen approved enums to arbitrary strings.
5. Add an API-spec/type smoke test or `tsc` fixture ensuring representative approved JSON objects satisfy the DTO types.
6. Run and observe type failures before updating downstream consumers:

```bash
pnpm test -- src/lib/api/pages.test.ts
pnpm build
```

7. Commit:

```bash
git add docs/api-spec.json docs/api_spec_doc.md src/lib/api/types.ts src/lib/api/pages.test.ts
git commit -m "feat: 백엔드 최종 계약 타입 반영"
```

## Task 2: Replace archive-window navigation with the B5 endpoint

**Files:**
- Modify: `src/lib/api/pages.ts`
- Modify: `src/lib/api/pages.test.ts`
- Modify: `src/lib/query-hooks.ts`
- Modify: `src/lib/query-hooks.test.tsx`
- Modify: `src/pages/market-overview/use-adjacent-snapshot-dates.ts`
- Modify: `src/pages/market-overview/use-adjacent-snapshot-dates.test.tsx`
- Modify: `src/app/market-overview-route-content.test.tsx`

1. Add a failing API-client test for:

```ts
getPageNavigation('2026-08-13', signal)
// GET /stock/api/pages/navigation?businessDate=2026-08-13
```

2. Add a dedicated query key/hook keyed only by business date. Do not reuse archive list cache entries.
3. Rewrite `useAdjacentSnapshotDates` to map `previousBusinessDate` and `nextBusinessDate` directly. Remove `WINDOW_DAYS`, `WINDOW_SIZE`, date shifting, sorting, and archive dependency.
4. A valid missing date with `pageExists=false` still enables server-provided previous/next navigation. Network/error/loading states keep both buttons disabled without guessing.
5. For an already loaded daily page, prefer its embedded `navigation` fields and use the standalone query only on the date-not-found route where no page DTO exists. Ensure only one request path runs for each state.
6. Run:

```bash
pnpm test -- src/lib/api/pages.test.ts src/lib/query-hooks.test.tsx src/pages/market-overview/use-adjacent-snapshot-dates.test.tsx src/app/market-overview-route-content.test.tsx
```

7. Commit:

```bash
git add src/lib/api/pages.ts src/lib/api/pages.test.ts src/lib/query-hooks.ts src/lib/query-hooks.test.tsx src/pages/market-overview/use-adjacent-snapshot-dates.ts src/pages/market-overview/use-adjacent-snapshot-dates.test.tsx src/app/market-overview-route-content.test.tsx
git commit -m "feat: 서버 기준 인접 페이지 탐색 적용"
```

## Task 3: Render B1 key points as text-first content

**Files:**
- Modify: `src/lib/view-models.ts`
- Modify: `src/lib/mappers/market.ts`
- Modify: `src/lib/mappers/market.test.ts`
- Modify: `src/pages/market-overview/decision-header-card.tsx`
- Modify: `src/pages/market-overview-page.test.tsx`

1. Add mapper tests for exactly ordered direction/driver/watch items, all four direction values, and empty failure output.
2. Add a `KeyPoint` view model preserving `kind`, fixed label, text, and optional direction. Map invalid runtime entries out; never synthesize prose from global headline or market summaries.
3. Render the items in the decision header as three short text blocks in server order. Text labels carry all meaning; direction color/icon may enhance but cannot be the only indication.
4. When `keyPoints=[]`, omit the entire key-point section. Keep global headline and partial issue presentation independent.
5. Add accessibility tests for visible labels and no empty landmark/heading on failure.
6. Run:

```bash
pnpm test -- src/lib/mappers/market.test.ts src/pages/market-overview-page.test.tsx
```

7. Commit:

```bash
git add src/lib/view-models.ts src/lib/mappers/market.ts src/lib/mappers/market.test.ts src/pages/market-overview/decision-header-card.tsx src/pages/market-overview-page.test.tsx
git commit -m "feat: 일간 핵심 포인트 표시"
```

## Task 4: Map and render B2 structured grounded analysis

**Files:**
- Modify: `src/lib/view-models.ts`
- Modify: `src/lib/mappers/cluster.ts`
- Modify: `src/lib/mappers/cluster.test.ts`
- Modify: `src/pages/cluster-detail/cluster-analysis.tsx`
- Modify: `src/pages/cluster-detail/cluster-analysis.test.tsx`
- Modify: `src/pages/cluster-detail-page.tsx`
- Modify: `src/pages/cluster-detail-page.test.tsx`

1. Replace `analysis: string[]` with typed status, generated time, issues, conflict status, and ordered sections/paragraphs/sentences. Preserve numeric source IDs for link targeting.
2. Add mapper tests for READY, PARTIAL conflict-check failure, UNAVAILABLE, FOUND conflicts, invalid references, and empty sections. The mapper must not infer section types from prose.
3. Render only returned nonempty sections with backend-provided fixed titles. Render paragraph sentences in order and expose source references as controls/links to matching article rows.
4. Give each article row a stable DOM target derived from `processedArticleId`; clicking a source reference scrolls/focuses the matching row without opening an external site.
5. For `FOUND`, show the conflict note and separately label conflicting sources. For `NOT_CHECKED`, show the approved restrained status copy; do not imply “no conflict.”
6. For `UNAVAILABLE`, render one explicit unavailable state and no empty section headings. Display `analysisGeneratedAt`, not cluster `lastUpdatedAt`, beside the analysis heading.
7. Remove comments and code describing the old backend text-array limitation.
8. Run:

```bash
pnpm test -- src/lib/mappers/cluster.test.ts src/pages/cluster-detail/cluster-analysis.test.tsx src/pages/cluster-detail-page.test.tsx
```

9. Commit:

```bash
git add src/lib/view-models.ts src/lib/mappers/cluster.ts src/lib/mappers/cluster.test.ts src/pages/cluster-detail src/pages/cluster-detail-page.tsx src/pages/cluster-detail-page.test.tsx
git commit -m "feat: 근거 기반 클러스터 분석 UI 적용"
```

## Task 5: Add theme catalog fetching and multi-select archive state

**Files:**
- Modify: `src/lib/api/archive.ts`
- Create: `src/lib/api/archive.test.ts`
- Modify: `src/lib/query-hooks.ts`
- Modify: `src/lib/query-hooks.test.tsx`
- Modify: `src/lib/app-state.ts`
- Modify: `src/lib/app-state.test.ts`
- Modify: `src/pages/archive-search/filter-copy.ts`
- Modify: `src/pages/archive-search/filter-copy.test.ts`

1. Add `getArchiveThemes()` and test the exact endpoint. Add `theme?: string[]`, `marketType?: 'US'|'KR'`, and `q?: string` to archive query parameters.
2. Verify the shared client serializes theme arrays as repeated query keys (`theme=A&theme=B`), not a comma-joined string. Add a client test if current serialization does not guarantee this.
3. Extend archive route state with `market`, `themes: string[]`, `q`, and page. Parse repeated `theme` values, deduplicate while preserving order, cap at 10, and reset page to 1 when any filter changes.
4. Preserve unknown theme codes from the URL until the catalog is loaded, then remove inactive/unknown codes with one replace-state update. Do not silently send them to the backend repeatedly.
5. Remove FAILED from the public archive status options and accepted route allowlist. Operations views remain unchanged.
6. Add query-key tests proving theme order is canonicalized so equivalent selections share one cache entry.
7. Run:

```bash
pnpm test -- src/lib/api/archive.test.ts src/lib/api/client.test.ts src/lib/query-hooks.test.tsx src/lib/app-state.test.ts src/pages/archive-search/filter-copy.test.ts
```

8. Commit:

```bash
git add src/lib/api src/lib/query-hooks.ts src/lib/query-hooks.test.tsx src/lib/app-state.ts src/lib/app-state.test.ts src/pages/archive-search/filter-copy.ts src/pages/archive-search/filter-copy.test.ts
git commit -m "feat: 아카이브 테마 검색 상태 추가"
```

## Task 6: Build the hierarchical multi-select archive filters

**Files:**
- Modify: `src/pages/archive-search/archive-search-filters.tsx`
- Modify: `src/pages/archive-search/archive-search-filters.test.tsx`
- Modify: `src/pages/archive-search-page.tsx`
- Modify: `src/pages/archive-search-page.test.tsx`
- Modify: `src/pages/archive-search/archive-results-table.tsx`
- Modify: `src/pages/archive-search/archive-results-table.test.tsx`

1. Add failing UI tests for nested roots/intermediates/leaves, multiple selected themes, parent selection, market selection, q input, clearing, submit, browser back/forward restoration, and loading/error catalog states.
2. Render a tree-based checkbox multi-select. Selecting a parent sends only that parent code; do not eagerly enumerate children in the URL because the backend owns descendant expansion.
3. Allow up to 10 selected codes. Prevent an 11th selection with visible explanatory text; keep all controls keyboard-operable and associate checkboxes with full hierarchy labels.
4. Add market radio/select for all/US/KR and q search input with client hints for 2–100 chars. Backend 422 remains authoritative; surface `INVALID_THEME` and validation messages in the existing archive error panel.
5. Include active filters in the applied summary and no-results copy. Send filters to both table query and pagination links.
6. Remove FAILED-only presentation branches from the public results table while keeping defensive unknown status rendering.
7. Run:

```bash
pnpm test -- src/pages/archive-search/archive-search-filters.test.tsx src/pages/archive-search-page.test.tsx src/pages/archive-search/archive-results-table.test.tsx
```

8. Commit:

```bash
git add src/pages/archive-search
git commit -m "feat: 계층형 테마 복수 검색 UI 추가"
```

## Task 7: Map and render B4 similar article groups

**Files:**
- Modify: `src/lib/view-models.ts`
- Modify: `src/lib/mappers/cluster.ts`
- Modify: `src/lib/mappers/cluster.test.ts`
- Modify: `src/pages/cluster-detail/cluster-article-controls.ts`
- Modify: `src/pages/cluster-detail/cluster-article-controls.test.ts`
- Modify: `src/pages/cluster-detail/cluster-articles-list.tsx`
- Modify: `src/pages/cluster-detail/cluster-articles-list.test.tsx`
- Modify: `src/pages/cluster-detail-page.tsx`

1. Extend each `ClusterArticle` with group ID, server representative flag, and exact duplicate count. Add grouping status/generated time/issue to `ClusterDetail`.
2. Map flat articles into group view models while preserving backend article order and group order. Validate one membership per article and at most one representative flag; malformed runtime groups fall back to ungrouped display, never destructive omission.
3. Apply source/query/sort filters to articles, then rebuild visible groups:
   - if the server representative remains, keep it;
   - otherwise choose the first remaining article in server order;
   - a one-visible-article group has no collapse toggle.
4. READY groups with 2+ visible articles render a representative row and accessible expand/collapse control. Show `exactDuplicateCount` as “원문 중복 N건” only when greater than zero; do not count similar members.
5. UNAVAILABLE renders the existing flat list, no collapse controls, and one nonblocking status message. It must not look like a page/analysis failure.
6. Keep pagination/show-more deterministic at group boundaries: do not split a visible group across pages. Increase visible capacity by enough whole groups to cover at least the existing `ARTICLE_PAGE_SIZE` increment.
7. Add tests for representative removal after filtering, singleton groups, exact counts, UNAVAILABLE, group-boundary pagination, and keyboard/ARIA state.
8. Run:

```bash
pnpm test -- src/lib/mappers/cluster.test.ts src/pages/cluster-detail/cluster-article-controls.test.ts src/pages/cluster-detail/cluster-articles-list.test.tsx src/pages/cluster-detail-page.test.tsx
```

9. Commit:

```bash
git add src/lib/view-models.ts src/lib/mappers/cluster.ts src/lib/mappers/cluster.test.ts src/pages/cluster-detail
git commit -m "feat: 유사 기사 그룹 UI 적용"
```

## Task 8: Complete frontend regression and contract verification

1. Update all hand-authored daily-page and cluster fixtures to the new required contract. Do not add optional markers merely to avoid fixture changes.
2. Search for and remove stale production references:

```bash
rg -n "summary\.analysis|analysis: string\[\]|WINDOW_DAYS|WINDOW_SIZE|status=FAILED|processedArticleId\?" src docs
```

Expected production result: none for removed contract/workaround; historical request docs may still describe the old state.
3. Run the full gates:

```bash
pnpm test
pnpm lint
pnpm build
pnpm knip
pnpm e2e
```

4. Run manual responsive/accessibility smoke checks at mobile and desktop sizes for:
   - empty/success key points;
   - READY/PARTIAL/UNAVAILABLE analysis;
   - parent and multiple theme selection;
   - no-results archive query;
   - READY/UNAVAILABLE article grouping;
   - missing-date previous/next navigation.
5. Inspect network requests to confirm no archive-window request remains for navigation, themes use repeated params, and no frontend call reaches Ollama/Gemini.
6. Commit final fixture/regression changes:

```bash
git add src tests docs
git commit -m "test: FE 백엔드 계약 통합 검증"
```

## Task 9: Coordinated deployment-readiness handoff

Do not deploy in this task. Produce a handoff note containing:

- backend commit range and migration order (`08` then `09`);
- frontend commit range;
- required Ollama environment values and verified `bge-m3` digest;
- B3 and B4 evaluation report links;
- complete backend/frontend test outputs;
- smoke-test payloads for all new endpoints/states;
- rollback scope limited to the coordinated release, without legacy dual-read/dual-write logic.

Use `superpowers:verification-before-completion` and review both repositories independently before declaring the implementation ready.
