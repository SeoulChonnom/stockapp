# B1/B2 AI Response Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Persist and serve fixed daily-page key points plus grounded, sectioned cluster analysis with sentence-level source and conflict evidence.

**Architecture:** Extend the existing Gemini summary stage rather than generating content in read APIs. Store key points in the `GLOBAL_HEADLINE` summary metadata and copy them into page snapshot metadata. Store structured cluster sections in the existing `CLUSTER_DETAIL_ANALYSIS.paragraphs_json`; the cluster read path joins the latest persisted summary. Isolate normalization and cross-reference validation in a pure module shared by batch tests and the assembler.

**Tech Stack:** FastAPI, Pydantic v2, Gemini JSON client, existing `ai_summary` JSONB fields, pytest/AnyIO.

---

## Task 1: Define strict Pydantic wire models

**Files:**
- Modify: `app/schemas/page.py`
- Modify: `app/schemas/cluster.py`
- Modify: `tests/domains/test_page_assembler.py`
- Modify: `tests/domains/test_cluster_assembler.py`

1. Add failing model/assembler tests for the exact B1 success shape. `DailyPageResponse.keyPoints` is required and success contains exactly three items in this order:

```json
[
  {"kind":"direction","label":"시장 방향","text":"주요 지수가 상승했습니다.","direction":"UP"},
  {"kind":"driver","label":"핵심 동인","text":"반도체 업종 강세가 상승을 이끌었습니다."},
  {"kind":"watch","label":"주의 포인트","text":"다음 거래일 금리 발표를 확인해야 합니다."}
]
```

2. Assert the direction enum accepts only `UP`, `DOWN`, `MIXED`, `FLAT`; driver/watch reject a `direction` property. Assert total failure is represented as `keyPoints: []`, never omitted or null.
3. Add failing B2 schema tests for:
   - removal of `summary.analysis`;
   - required `analysisStatus`, `analysisGeneratedAt`, `analysisIssues`, `conflictStatus`, `sections`;
   - fixed section kinds/titles/order;
   - required integer `processedArticleId`;
   - required article grouping fields described in B4, initially represented in the schema but populated in the B4 plan.
4. Add nested models/enums in `app/schemas/cluster.py`:

```python
class AnalysisSentenceResponse(BaseModel):
    text: str
    sourceArticleIds: list[int]
    conflictStatus: Literal['NOT_CHECKED', 'NONE', 'FOUND']
    conflictingSourceArticleIds: list[int]
    conflictNote: str | None

class AnalysisParagraphResponse(BaseModel):
    sentences: list[AnalysisSentenceResponse]

class AnalysisSectionResponse(BaseModel):
    kind: Literal['background', 'impact', 'related', 'outlook']
    title: str
    paragraphs: list[AnalysisParagraphResponse]
```

Add `AnalysisIssueResponse(code: Literal[...], message: str)` for the four approved issue codes. Configure the key-point discriminated union with `extra='forbid'` so a direction on driver/watch is rejected instead of ignored.

5. Use Pydantic model validators to enforce:
   - every source list is nonempty and unique;
   - source and conflicting-source IDs are disjoint;
   - `FOUND` requires conflicting IDs and a nonblank note;
   - non-`FOUND` requires empty conflicting IDs and null note;
   - `UNAVAILABLE` requires `sections=[]`, `analysisGeneratedAt=None`, and aggregate `NOT_CHECKED`.
6. Make `processedArticleId: int` required in both `app/schemas/page.py::ArticleLinkResponse` and `app/schemas/cluster.py::ClusterArticleResponse`.
7. Run and observe failures before schema edits, then run until green:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/domains/test_page_assembler.py tests/domains/test_cluster_assembler.py -q
```

8. Commit:

```bash
git add app/schemas/page.py app/schemas/cluster.py tests/domains/test_page_assembler.py tests/domains/test_cluster_assembler.py
git commit -m "feat: AI 응답 스키마 계약 정의"
```

## Task 2: Build pure key-point validation and fallback behavior

**Files:**
- Create: `app/batch/ai_output_contracts.py`
- Create: `tests/batch/test_ai_output_contracts.py`

1. Write failing unit tests for `normalize_key_points(payload)`. It returns either the exact three normalized items or a failure result containing:

```python
{
    'keyPoints': [],
    'issue': {
        'category': 'AI_SUMMARY',
        'code': 'KEY_POINTS_GENERATION_FAILED',
        'message': '핵심 포인트를 생성하지 못했습니다.',
    },
}
```

2. Cover wrong length, wrong order, wrong fixed label, blank text, invalid direction, direction on driver/watch, non-object payload, and extra/missing kinds.
3. Implement immutable constants for kind order and labels plus typed normalization helpers. Do not repair semantic errors by reordering or guessing; reject the entire key-point payload to `[]`.
4. Add `aggregate_conflict_status(sentences)` with priority `FOUND > NOT_CHECKED > NONE` and tests for empty/mixed input.
5. Add `validate_analysis_sections(payload, valid_article_ids)` returning normalized sections plus machine-readable issues. Cover:
   - unsupported section kind/title/order;
   - empty sections omitted from output;
   - unknown or missing source IDs → `INVALID_SOURCE_REFERENCE`;
   - no grounded sentences → `NO_GROUNDED_SENTENCES`;
   - malformed conflict evidence → `CONFLICT_CHECK_FAILED`;
   - provider/malformed top-level response → `ANALYSIS_GENERATION_FAILED`.
6. The validator must not silently drop a bad citation while keeping its sentence. Any invalid source reference makes the analysis `UNAVAILABLE`; conflict-check-only failures may produce `PARTIAL` with sentence status `NOT_CHECKED` if grounding remains valid.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ai_output_contracts.py -q
```

8. Commit:

```bash
git add app/batch/ai_output_contracts.py tests/batch/test_ai_output_contracts.py
git commit -m "feat: AI 출력 검증기 추가"
```

## Task 3: Generate and persist B1 key points independently

**Files:**
- Modify: `app/batch/providers/llm_provider.py`
- Modify: `app/batch/steps/ai_summary_generators.py`
- Modify: `app/batch/steps/generate_ai_summaries.py`
- Modify: `tests/batch/test_llm_provider.py`
- Modify: `tests/batch/test_ai_summary_normalization.py`
- Modify: `tests/batch/test_remaining_batch_step_contracts.py`

1. Add provider serialization tests for `summarize_key_points(clusters, indices)`. The system prompt must demand the three fixed objects and closed direction enum; the user payload contains only required cluster/index evidence.
2. Add generator tests proving the headline and key-point calls are independent:
   - headline success + key-point failure preserves headline and stores empty key points;
   - headline failure + key-point success preserves key points;
   - both success stores both;
   - malformed key points produce the fixed issue.
3. Introduce `_generate_global_outputs` which runs `_generate_global_headline` and `_generate_key_points` under the existing bounded summary target. Do not add a new `ai_summary_type_enum` value. Merge into the persisted `GLOBAL_HEADLINE.metadata_json`:

```python
{
    **headline_metadata,
    'keyPoints': key_point_result['keyPoints'],
    'keyPointIssue': key_point_result['issue'],
}
```

4. Catch provider exhaustion and malformed key-point output inside the key-point branch so it cannot replace a valid headline result. Preserve cancellation behavior (`asyncio.CancelledError` must propagate).
5. In `_persist_summary_result`, detect `keyPointIssue`, call `context.add_partial('KEY_POINTS_GENERATION_FAILED', key_point_issue['message'])`, and add one sanitized warning event without marking the `GLOBAL_HEADLINE` summary itself as fallback when its headline succeeded.
6. Update `PROMPT_VERSION` because persisted output semantics changed.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_llm_provider.py tests/batch/test_ai_summary_normalization.py tests/batch/test_remaining_batch_step_contracts.py -q
```

8. Commit:

```bash
git add app/batch/providers/llm_provider.py app/batch/steps/ai_summary_generators.py app/batch/steps/generate_ai_summaries.py tests/batch
git commit -m "feat: 일간 핵심 포인트 생성 및 저장"
```

## Task 4: Copy B1 output into the immutable page snapshot

**Files:**
- Modify: `app/batch/steps/build_page_snapshot.py`
- Modify: `app/domains/pages/assembler.py`
- Modify: `tests/batch/test_build_page_snapshot_rebuild.py`
- Modify: `tests/domains/test_page_assembler.py`
- Modify: `tests/api/test_pages.py`

1. Add failing snapshot tests for success and failure. `create_page(metadata_json={'keyPoints': key_points, 'issues': issues, 'warnings': warnings})` must include `keyPoints`; failure must append the `KEY_POINTS_GENERATION_FAILED` issue and result in `PARTIAL`.
2. Update `_structured_page_issues` so the explicit B1 issue is not collapsed into generic `AI_SUMMARY_FALLBACK`.
3. Copy key points from the `GLOBAL_HEADLINE` summary metadata into `market_daily_page.metadata_json.keyPoints`. On rebuild, copy the source page metadata without calling a provider.
4. Update the page assembler to read `keyPoints` only from persisted page metadata and validate through the response model. Missing legacy metadata maps to `[]`; current-batch generation failure has the explicit issue and partial status.
5. Make page article-link reads exclude legacy rows whose `processed_article_id` became null; current snapshot writes must reject a null ID. This keeps the required API field truthful without inventing identifiers.
6. Add API assertions that key points always exist and are not regenerated between repeated reads.
6. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_build_page_snapshot_rebuild.py tests/domains/test_page_assembler.py tests/api/test_pages.py -q
```

7. Commit:

```bash
git add app/batch/steps/build_page_snapshot.py app/domains/pages/assembler.py tests/batch/test_build_page_snapshot_rebuild.py tests/domains/test_page_assembler.py tests/api/test_pages.py
git commit -m "feat: 페이지 스냅샷에 핵심 포인트 반영"
```

## Task 5: Generate grounded B2 sections

**Files:**
- Modify: `app/batch/providers/llm_provider.py`
- Modify: `app/batch/steps/ai_summary_generators.py`
- Modify: `tests/batch/test_llm_provider.py`
- Modify: `tests/batch/test_ai_summary_normalization.py`

1. Replace the cluster-detail prompt contract. Each input article includes its integer `processedArticleId`; output requires `sections[].paragraphs[].sentences[]` with citation/conflict fields.
2. Prompt rules must state:
   - section order and Korean fixed titles from the approved design;
   - omit empty sections;
   - every sentence cites one or more supplied IDs;
   - no invented IDs;
   - conflict status semantics and disjoint ID sets.
3. Add tests for a fully grounded `READY` result, grounded result with conflict-check degradation (`PARTIAL`), unknown citation (`UNAVAILABLE`), empty grounded output (`UNAVAILABLE`), and provider failure (`UNAVAILABLE`).
4. Update `_generate_cluster_detail_summary` to call `validate_analysis_sections`. Persist normalized sections in `paragraphs`; persist `analysisStatus`, `analysisIssues`, and aggregate `conflictStatus` in `metadata_json`.
5. Fallback must be explicit, not the legacy `news_cluster.analysis_paragraphs_json` string list:

```python
{
    'paragraphs': [],
    'metadata_json': {
        'analysisStatus': 'UNAVAILABLE',
        'analysisIssues': [{
            'code': 'ANALYSIS_GENERATION_FAILED',
            'message': '분석을 생성하지 못했습니다.',
        }],
        'conflictStatus': 'NOT_CHECKED',
    },
}
```

6. A B2 `UNAVAILABLE` result must not fail the cluster endpoint and must not change the daily page status.
7. Run the focused batch tests until green.
8. Commit:

```bash
git add app/batch/providers/llm_provider.py app/batch/steps/ai_summary_generators.py tests/batch/test_llm_provider.py tests/batch/test_ai_summary_normalization.py
git commit -m "feat: 근거 기반 클러스터 분석 생성"
```

## Task 6: Read the latest persisted B2 analysis

**Files:**
- Modify: `app/db/repositories/ai_summary_repo.py`
- Modify: `app/domains/clusters/router.py`
- Modify: `app/domains/clusters/service.py`
- Modify: `app/domains/clusters/assembler.py`
- Modify: `tests/repositories/test_ai_summary_repo.py`
- Modify: `tests/domains/test_clusters_service.py`
- Modify: `tests/domains/test_cluster_assembler.py`
- Modify: `tests/api/test_clusters.py`

1. Add a repository test requiring `get_latest_cluster_summary` to select the latest row by `attempt_no DESC, generated_at DESC, id DESC`, irrespective of success/fallback. This ensures a later failed analysis is visible as `UNAVAILABLE` instead of silently serving stale success.
2. Inject `AiSummaryRepository` into `ClustersService` through `get_clusters_service`; fetch the latest `CLUSTER_DETAIL_ANALYSIS` alongside cluster/articles.
3. Build `valid_article_ids` from the same response's articles and revalidate persisted sections defensively. `analysisGeneratedAt` comes from `ai_summary.generated_at`, never `news_cluster.updated_at`.
4. Assemble the exact summary contract. For a missing legacy summary, use:

```json
{
  "short": null,
  "long": null,
  "analysisStatus": "UNAVAILABLE",
  "analysisGeneratedAt": null,
  "analysisIssues": [{"code":"ANALYSIS_GENERATION_FAILED","message":"분석을 사용할 수 없습니다."}],
  "conflictStatus": "NOT_CHECKED",
  "sections": []
}
```

5. Preserve `short`/`long` from the cluster record; only the analysis metadata/sections comes from `ai_summary`.
6. Ensure representative/article payloads use required integer IDs; missing processed records remain an existing not-found/data-integrity error rather than emitting null IDs.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_ai_summary_repo.py tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py -q
```

8. Commit:

```bash
git add app/db/repositories/ai_summary_repo.py app/domains/clusters tests/repositories/test_ai_summary_repo.py tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py
git commit -m "feat: 구조화된 클러스터 분석 응답 제공"
```

## Task 7: Verify B1/B2 contract coverage

1. Update `tests/contracts/test_openapi_read_routes.py` to assert closed enums, required keys, removed legacy `analysis`, required processed IDs, and nested source/conflict fields.
2. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ai_output_contracts.py tests/batch/test_llm_provider.py tests/batch/test_ai_summary_normalization.py tests/batch/test_remaining_batch_step_contracts.py tests/batch/test_build_page_snapshot_rebuild.py tests/domains/test_page_assembler.py tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/repositories/test_ai_summary_repo.py tests/api/test_pages.py tests/api/test_clusters.py tests/contracts/test_openapi_read_routes.py
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests
git diff --check
```

3. Inspect one success and one unavailable JSON response against the examples in the approved design.
4. Commit contract assertions:

```bash
git add tests/contracts/test_openapi_read_routes.py
git commit -m "test: AI 응답 계약 고정"
```
