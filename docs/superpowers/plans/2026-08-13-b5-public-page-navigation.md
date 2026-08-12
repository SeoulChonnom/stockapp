# B5 Public Page Selection and Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Make every public page read use the latest `READY`/`PARTIAL` version and add date navigation that works even when the requested date has no page.

**Architecture:** Centralize the public-status predicate and latest-public-per-date SQL in `PageSnapshotRepository`. Reuse the same repository methods from daily page reads, embedded navigation, the standalone navigation endpoint, and archive queries. Explicit page-id and explicit version reads retain access to failed versions.

**Tech Stack:** FastAPI, Pydantic v2, SQLAlchemy text queries, PostgreSQL, pytest/AnyIO.

---

## Task 1: Lock the repository selection semantics with failing tests

**Files:**
- Modify: `tests/repositories/test_page_snapshot_repo.py`

1. Add query-contract tests proving `get_latest_public_page_header()` and the default branch of `get_page_header_by_business_date()` filter to `READY`/`PARTIAL` before ordering by `business_date DESC, version_no DESC, id DESC`.
2. Add a test proving `get_page_header_by_business_date(date, version_no=3)` does not apply the public filter.
3. Add tests proving `exists_public_page_for_business_date()` and `get_adjacent_public_business_dates()` ignore dates containing only failed pages.
4. Add archive SQL tests proving selection order is:

```sql
WITH latest_public AS (
    SELECT DISTINCT ON (business_date)
        id, business_date, version_no, page_title, status,
        global_headline, generated_at, partial_message
    FROM stock.market_daily_page
    WHERE status IN ('READY', 'PARTIAL')
    ORDER BY business_date DESC, version_no DESC, id DESC
)
SELECT
    id AS "pageId", business_date AS "businessDate",
    page_title AS "pageTitle", global_headline AS "headlineSummary",
    status, generated_at AS "generatedAt", partial_message AS "partialMessage"
FROM latest_public
ORDER BY business_date DESC
LIMIT :limit OFFSET :offset
```

The repository appends bound date/status/search predicates between `FROM latest_public` and `ORDER BY`; the test must assert those predicates occur outside the CTE.

5. Cover the critical `status=READY` case: if the latest public version is `PARTIAL`, filtering must exclude the date rather than choosing an older `READY` version.
6. Run and observe failure:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_page_snapshot_repo.py -q
```

Expected failure: missing public methods and archive SQL selecting/filtering in the old order.

## Task 2: Implement the repository public-read primitives

**Files:**
- Modify: `app/db/repositories/page_snapshot_repo.py`
- Test: `tests/repositories/test_page_snapshot_repo.py`

1. Add one module constant:

```python
PUBLIC_PAGE_STATUSES: tuple[str, ...] = ('READY', 'PARTIAL')
```

2. Add `get_latest_public_page_header`, `exists_public_page_for_business_date`, and `get_adjacent_public_business_dates`.
3. Change only the `version_no is None` branch of `get_page_header_by_business_date` to filter public statuses before calculating `is_latest`. Preserve explicit version and page-id reads.
4. Make `is_latest` mean latest publicly displayable version for public reads. Preserve all-version behavior for the explicit version picker unless a later UI requirement says otherwise.
5. Rewrite archive list/count around the `latest_public` CTE. Apply `fromDate`, `toDate`, and `status` outside the CTE.
6. Keep query parameters bound; do not interpolate user values.
7. Run the repository tests until green, then run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_page_snapshot_repo.py tests/domains/test_pages_service.py -q
```

8. Commit:

```bash
git add app/db/repositories/page_snapshot_repo.py tests/repositories/test_page_snapshot_repo.py tests/domains/test_pages_service.py
git commit -m "fix: 공개 페이지 선택 기준 통일"
```

## Task 3: Add the standalone navigation contract test-first

**Files:**
- Modify: `tests/api/test_pages.py`
- Modify: `tests/domains/test_pages_service.py`
- Modify: `app/schemas/page.py`
- Modify: `app/domains/pages/service.py`
- Modify: `app/domains/pages/router.py`

1. Add service tests for existing, missing, earliest, latest, and failed-only requested dates. The service result must always be:

```python
{
    'businessDate': date(2026, 8, 13),
    'pageExists': False,
    'previousBusinessDate': date(2026, 8, 12),
    'nextBusinessDate': date(2026, 8, 14),
}
```

2. Add API tests for:
   - `GET /stock/api/pages/navigation?businessDate=2026-08-13` returns HTTP 200 and all four required keys.
   - a syntactically valid missing date still returns HTTP 200.
   - malformed dates return FastAPI HTTP 422.
3. Run the focused tests and observe missing schema/route failures.
4. Add `PageDateNavigationResponse` to `app/schemas/page.py` with required `businessDate`, `pageExists`, `previousBusinessDate`, and `nextBusinessDate` fields; export it in `__all__`.
5. Add `PagesService.get_date_navigation(business_date)` using the two public repository methods.
6. Register `/navigation` before `/{pageId}` in `app/domains/pages/router.py` so the static path cannot be captured by the dynamic integer route.
7. Add a typed `ApiSuccess[PageDateNavigationResponse]` response and keep the endpoint authenticated through `UserDep` like the other page reads.
8. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/test_pages.py tests/domains/test_pages_service.py -q
```

9. Commit:

```bash
git add app/schemas/page.py app/domains/pages/service.py app/domains/pages/router.py tests/api/test_pages.py tests/domains/test_pages_service.py
git commit -m "feat: 페이지 날짜 탐색 API 추가"
```

## Task 4: Reuse public navigation in page assembly and archive service

**Files:**
- Modify: `app/domains/pages/service.py`
- Modify: `app/domains/archive/service.py`
- Modify: `app/domains/archive/router.py`
- Modify: `tests/domains/test_pages_service.py`
- Modify: `tests/domains/test_archive_service.py`
- Modify: `tests/api/test_pages.py`

1. Add failing tests proving `get_latest_page` calls the public latest method and daily-page embedded navigation excludes failed-only dates.
2. Remove `FAILED` from public archive accepted statuses in both the router literal and `ARCHIVE_STATUSES`.
3. Update the archive error response documentation to use HTTP 422 validation semantics for invalid status input; keep framework-generated literal validation consistent with the schema.
4. Verify explicit `GET /pages/{pageId}` and `versionNo` reads still return failed pages in their existing tests.
5. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/test_pages.py tests/domains/test_pages_service.py tests/domains/test_archive_service.py tests/repositories/test_page_snapshot_repo.py -q
```

6. Commit:

```bash
git add app/domains/pages app/domains/archive tests/api/test_pages.py tests/domains/test_pages_service.py tests/domains/test_archive_service.py
git commit -m "fix: 공개 탐색에서 실패 페이지 제외"
```

## Task 5: Verify the B5 slice

1. Add/adjust OpenAPI assertions in `tests/contracts/test_openapi_read_routes.py` for the navigation endpoint, required response keys, date format, and public archive status enum.
2. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/api/test_pages.py tests/domains/test_pages_service.py tests/domains/test_archive_service.py tests/repositories/test_page_snapshot_repo.py tests/contracts/test_openapi_read_routes.py
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests
git diff --check
```

3. Review the SQL for these three fixtures before completion:
   - older `READY`, newer `FAILED` → public result is the older `READY`;
   - older `READY`, newer `PARTIAL` → public result is the newer `PARTIAL`;
   - only `FAILED` → public date does not exist.
4. Commit contract assertions separately if they were not included above:

```bash
git add tests/contracts/test_openapi_read_routes.py
git commit -m "test: 공개 페이지 탐색 계약 고정"
```
