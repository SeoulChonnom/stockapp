# B4 Article Similarity Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan.

**Goal:** Distinguish raw exact duplicates from similar processed articles, group every cluster article deterministically using local Ollama `bge-m3`, and serve persisted grouping metadata with safe singleton fallback.

**Architecture:** Add a dedicated `GROUP_SIMILAR_ARTICLES` batch step after cluster construction/theme classification. The step batch-embeds one cluster at a time, combines dense and deterministic lexical scores, applies contradiction vetoes and complete-link grouping, and atomically replaces persisted groups. Snapshot construction copies status and article membership fields; API reads never call Ollama.

**Tech Stack:** Ollama `/api/embed`, `bge-m3`, httpx, pure-Python vector/scoring code, PostgreSQL, FastAPI/Pydantic, pytest/AnyIO.

---

## Task 1: Add source and snapshot persistence

**Files:**
- Create: `db/migrations/20260813_09_article_similarity_groups.sql`
- Modify: `db/schema_postgresql.sql`
- Modify: `tests/db/test_schema_migrations.py`
- Modify: `tests/db/test_migrations_postgresql.py`

1. Add failing static/live migration tests for:

```sql
ALTER TABLE news_cluster
    ADD COLUMN article_grouping_status TEXT NOT NULL DEFAULT 'UNAVAILABLE',
    ADD COLUMN article_grouping_generated_at TIMESTAMPTZ NULL,
    ADD COLUMN article_grouping_issue_code TEXT NULL
        DEFAULT 'SIMILARITY_GROUPING_FAILED';

CREATE TABLE news_cluster_similar_group (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cluster_id BIGINT NOT NULL REFERENCES news_cluster(id) ON DELETE CASCADE,
    group_rank SMALLINT NOT NULL CHECK (group_rank > 0),
    representative_article_id BIGINT NOT NULL,
    algorithm_version TEXT NOT NULL,
    generated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (cluster_id, group_rank),
    FOREIGN KEY (cluster_id, representative_article_id)
        REFERENCES news_cluster_article(cluster_id, processed_article_id)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE TABLE news_cluster_similar_group_article (
    similar_group_id BIGINT NOT NULL
        REFERENCES news_cluster_similar_group(id) ON DELETE CASCADE,
    processed_article_id BIGINT NOT NULL
        REFERENCES news_article_processed(id) ON DELETE RESTRICT,
    similarity_score DOUBLE PRECISION NOT NULL,
    exact_duplicate_count INTEGER NOT NULL CHECK (exact_duplicate_count >= 0),
    is_representative BOOLEAN NOT NULL,
    article_rank SMALLINT NOT NULL CHECK (article_rank > 0),
    PRIMARY KEY (similar_group_id, processed_article_id),
    UNIQUE (similar_group_id, article_rank)
);
```

2. Constrain source and snapshot status to `READY|UNAVAILABLE`; require generated time only for `READY`, and require `SIMILARITY_GROUPING_FAILED` only for `UNAVAILABLE`.
3. Add to `market_daily_page_market_cluster`: status, generated time, issue code, and algorithm version. Add to `market_daily_page_article_link`: nullable `similar_group_rank`, required `is_similar_group_representative DEFAULT TRUE`, and required nonnegative `exact_duplicate_count DEFAULT 0`.
4. Add indexes for group cluster/rank and reverse article lookup. The migration must be idempotent and transaction-wrapped.
5. Do not add vector columns or the `vector` extension.
6. Run static tests, then live migration tests when a DSN is available.
7. Commit:

```bash
git add db/schema_postgresql.sql db/migrations/20260813_09_article_similarity_groups.sql tests/db/test_schema_migrations.py tests/db/test_migrations_postgresql.py
git commit -m "feat: 유사 기사 그룹 스키마 추가"
```

## Task 2: Add Ollama settings and a validated embedding client

**Files:**
- Modify: `app/core/settings.py`
- Create: `app/batch/providers/ollama_embedding_provider.py`
- Create: `tests/batch/test_ollama_embedding_provider.py`
- Modify: `tests/core/test_settings.py`

1. Add failing settings tests for environment aliases and bounds:

```text
STOCKAPP_OLLAMA_BASE_URL       default http://localhost:11434
STOCKAPP_OLLAMA_EMBED_MODEL    default bge-m3
STOCKAPP_OLLAMA_TIMEOUT_SECONDS default 30, >0
STOCKAPP_OLLAMA_MAX_RETRIES    default 2, range 0..2
STOCKAPP_SIMILARITY_INPUT_CHARS default 2048, >0
```

The character cap is the deterministic approximation for the approved 512-token input limit; record it in the algorithm version.
2. Add mocked-httpx tests requiring one request per cluster:

```json
{"model":"bge-m3","input":["삼성전자 실적 개선 영업이익 증가","삼성전자 분기 실적 시장 예상 상회"],"truncate":false}
```

3. Build each input as canonical title + source summary, or body excerpt when summary is absent, normalize whitespace, and cap it before the request.
4. Validate HTTP shape, vector count equals input count, nonzero shared dimension, numeric types, and finite values. Normalize no vectors in the client; cosine code owns that.
5. Retry only connection errors, timeouts, HTTP 408/429, and 5xx, at most two retries after the initial attempt. Do not retry 4xx model-not-found responses, invalid JSON, dimension/count mismatch, NaN, or infinity. Propagate `asyncio.CancelledError`.
6. Never include base URL, model filesystem details, raw vectors, or article content in public exceptions/log fields.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/core/test_settings.py tests/batch/test_ollama_embedding_provider.py -q
```

8. Commit:

```bash
git add app/core/settings.py app/batch/providers/ollama_embedding_provider.py tests/core/test_settings.py tests/batch/test_ollama_embedding_provider.py
git commit -m "feat: Ollama 임베딩 제공자 추가"
```

## Task 3: Implement deterministic pair scoring and contradiction vetoes

**Files:**
- Create: `app/batch/article_similarity.py`
- Create: `tests/batch/test_article_similarity.py`

1. Write failing tests for safe cosine similarity: identical, orthogonal, negative, zero vector, dimension mismatch, and non-finite values.
2. Implement the lexical feature extractor using NFC/casefold/whitespace normalization and deterministic regex tokenization. Preserve separate sets for content tokens, normalized numeric values/percentages, ISO/Korean dates, direction terms, uppercase ticker-like tokens, and organization/name tokens present verbatim in both texts.
3. Compute lexical score from bounded `[0,1]` components:
   - title token Dice overlap;
   - full-input token Dice overlap;
   - exact numeric/date agreement;
   - ticker/name/organization token agreement.
   Keep component weights in an immutable `SimilarityParameters` value selected by Task 7; do not hide numbers in prompt text.
4. Implement `combined = dense * dense_weight + lexical * lexical_weight`, requiring weights nonnegative and sum to 1.
5. Add contradiction vetoes independent of combined score:
   - different material numeric values attached to the same unit/metric;
   - different event dates when both are explicit;
   - opposing directional claims (`rise/gain/increase` versus `fall/loss/decrease`, including Korean equivalents).
6. Conservative ambiguity rule: if a structured value cannot be paired to a comparable token, it cannot create a veto by itself. Tests must cover false-veto boundaries such as two unrelated numbers in one article.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_article_similarity.py -q
```

8. Commit:

```bash
git add app/batch/article_similarity.py tests/batch/test_article_similarity.py
git commit -m "feat: 기사 유사도 점수와 상충 규칙 추가"
```

## Task 4: Implement complete-link grouping and representative selection

**Files:**
- Modify: `app/batch/article_similarity.py`
- Modify: `tests/batch/test_article_similarity.py`

1. Add failing tests proving chain prevention: A–B and B–C above threshold but A–C below threshold must not create `{A,B,C}`.
2. Sort candidate articles by `publishedAt DESC, processedArticleId ASC`. Precompute every unordered pair exactly once.
3. Place an article into the first existing group only if every pair with current members passes the threshold and no pair has a contradiction veto. Otherwise create a singleton group.
4. Rank groups deterministically by representative published time descending, then representative processed ID ascending. Assign contiguous ranks starting at 1.
5. Compute representative score:

```text
average in-group combined similarity × 0.70
+ information completeness          × 0.20
+ normalized recency                × 0.10
```

Information completeness is the fraction present among source summary, body excerpt, publisher, origin link, and valid published time. Exact duplicate count and existing cluster representative have no weight. Tie-break with published time descending and processed ID ascending.
6. Store each member's similarity score as its average similarity to the other group members; singleton score is `1.0`. Mark exactly one representative and contiguous article ranks.
7. Add property-style parametrized tests: every article appears exactly once, every group has one representative, every within-group pair passes, repeated input gives identical serialized output.
8. Run the focused tests and commit:

```bash
git add app/batch/article_similarity.py tests/batch/test_article_similarity.py
git commit -m "feat: complete-link 유사 기사 그룹 생성"
```

## Task 5: Add exact duplicate counts and group repositories

**Files:**
- Modify: `app/db/repositories/projections.py`
- Modify: `app/db/repositories/cluster_repo.py`
- Modify: `app/db/repositories/news_cluster_write_repo.py`
- Create: `app/db/repositories/article_group_repo.py`
- Create: `tests/repositories/test_article_group_repo.py`
- Modify: `tests/repositories/test_cluster_repo.py`

1. Add a repository query that counts raw mappings per processed article and returns `GREATEST(COUNT(DISTINCT raw_article_id) - 1, 0)`. Similar processed articles never affect this value.
2. Add read/write projections for groups and members.
3. Implement `replace_cluster_groups(cluster_id, result)` as one transaction unit:
   - delete existing groups (cascades members);
   - insert groups and members;
   - update cluster status/generatedAt/issue;
   - validate that persisted member IDs exactly equal current cluster member IDs.
4. Add `mark_grouping_unavailable_with_singletons(cluster_id, articles, exact_counts, algorithm_version)` that still persists one group per article, exact counts, representative true, and null public generated time with issue `SIMILARITY_GROUPING_FAILED`.
5. Add detail read methods that return source grouping status plus ranked memberships. No runtime reconstruction from embeddings.
6. Test exact count 0/1/many, replacement, rollback, membership mismatch, status constraints, and singleton fallback.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/repositories/test_article_group_repo.py tests/repositories/test_cluster_repo.py -q
```

8. Commit:

```bash
git add app/db/repositories/projections.py app/db/repositories/cluster_repo.py app/db/repositories/news_cluster_write_repo.py app/db/repositories/article_group_repo.py tests/repositories
git commit -m "feat: 유사 기사 그룹 저장소 추가"
```

## Task 6: Add the cluster-isolated batch step

**Files:**
- Create: `app/batch/steps/group_similar_articles.py`
- Modify: `app/batch/steps/__init__.py`
- Modify: `app/batch/orchestrators/market_daily.py`
- Create: `tests/batch/test_group_similar_articles_step.py`
- Modify: `tests/batch/test_market_daily_orchestrator.py`

1. Add failing orchestrator tests requiring `GROUP_SIMILAR_ARTICLES` after `BUILD_CLUSTERS` and, when B3 candidate B exists, after `CLASSIFY_CLUSTER_THEMES`; it remains before `COLLECT_MARKET_INDICES`.
2. For each cluster, load ordered articles and exact counts, build capped inputs, call Ollama once with the whole string array, compute all pairs, group, and replace persistence.
3. Isolate failures per cluster. On any final provider/validation/scoring error, log a sanitized batch event and persist singleton groups with `UNAVAILABLE`; continue with remaining clusters and do not call `context.add_partial`.
4. Configuration/catalog/programming errors discovered before cluster iteration may fail the step. `CancelledError`, lease loss, and database errors must propagate rather than becoming public fallbacks.
5. Include model, input cap, lexical feature version, weights, threshold, veto version, and grouping version in `algorithm_version`. Never persist vectors.
6. Support checkpoint resume by treating a cluster with current algorithm version and complete membership as done; otherwise replace it deterministically.
7. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_group_similar_articles_step.py tests/batch/test_market_daily_orchestrator.py -q
```

8. Commit:

```bash
git add app/batch/steps/group_similar_articles.py app/batch/steps/__init__.py app/batch/orchestrators/market_daily.py tests/batch/test_group_similar_articles_step.py tests/batch/test_market_daily_orchestrator.py
git commit -m "feat: 유사 기사 그룹 배치 단계 추가"
```

## Task 7: Calibrate one global parameter set

**Files:**
- Create: `scripts/calibrate_article_similarity.py`
- Create: `tests/fixtures/article_similarity_pairs.json`
- Create: `tests/batch/test_article_similarity_evaluation.py`
- Create: `docs/evaluations/2026-08-13-article-similarity.md`
- Modify: `app/batch/article_similarity.py`

1. Build at least 300 manually labeled processed-article pairs across KR/US. Labels are `SAME_EVENT`, `OTHER_EVENT`, and `HARD_NEGATIVE`; hard negatives include numeric, date, and direction contradictions. Store IDs and limited titles/summaries, not full article bodies.
2. Freeze a stratified 70% calibration / 30% holdout split by pair ID hash before parameter search.
3. On calibration only, grid-search lexical component weights, dense/lexical weights, and one global threshold. Optimize precision first, then same-event recall, then lower false merges; do not create market-specific thresholds.
4. Freeze the chosen `SimilarityParameters` and evaluate holdout once. Required gate:
   - group/pair precision >=95%;
   - same-event recall >=85%;
   - other-event false merge <=3%;
   - hard-negative false merges = 0;
   - repeated grouping determinism =100%;
   - per-cluster p95 processing time <=30 seconds on the documented evaluation host.
5. Make a small synthetic fixture test verify metric/split/grid code without Ollama. The live calibration script uses the local configured Ollama and exits nonzero on gate failure.
6. Record dataset hash, Ollama version, `bge-m3` model digest, selected parameters, algorithm version, calibration metrics, holdout metrics, and runtime p95 in the evaluation document.
7. Commit only a parameter set that passes the holdout gate:

```bash
git add scripts/calibrate_article_similarity.py tests/fixtures/article_similarity_pairs.json tests/batch/test_article_similarity_evaluation.py docs/evaluations/2026-08-13-article-similarity.md app/batch/article_similarity.py
git commit -m "test: 유사 기사 임계값 보정"
```

## Task 8: Copy grouping into page snapshots

**Files:**
- Modify: `app/db/repositories/page_snapshot_write_repo.py`
- Modify: `app/db/repositories/page_snapshot_repo.py`
- Modify: `app/batch/steps/build_page_snapshot.py`
- Modify: `tests/repositories/test_page_snapshot_write_repo.py`
- Modify: `tests/batch/test_build_page_snapshot_rebuild.py`

1. Extend source cluster/article-link queries to include status, generated time, issue, algorithm version, group rank, representative flag, and exact count.
2. Copy cluster grouping status to each snapshot cluster and membership fields to each matching snapshot article link.
3. Source `READY` must have every article mapped to exactly one group. Treat missing membership as data-integrity failure, not a synthesized ready result.
4. Source `UNAVAILABLE` copies singleton ranks and exact counts but public generated time remains null.
5. Rebuild copies the old snapshot rows exactly; it never calls Ollama or reads newer source groups.
6. Run snapshot-focused tests and commit:

```bash
git add app/db/repositories/page_snapshot_write_repo.py app/db/repositories/page_snapshot_repo.py app/batch/steps/build_page_snapshot.py tests/repositories/test_page_snapshot_write_repo.py tests/batch/test_build_page_snapshot_rebuild.py
git commit -m "feat: 스냅샷에 기사 그룹 정보 복사"
```

## Task 9: Serve grouping in cluster detail

**Files:**
- Modify: `app/schemas/cluster.py`
- Modify: `app/domains/clusters/service.py`
- Modify: `app/domains/clusters/assembler.py`
- Modify: `tests/domains/test_clusters_service.py`
- Modify: `tests/domains/test_cluster_assembler.py`
- Modify: `tests/api/test_clusters.py`

1. Complete the B4 schema introduced in the B1/B2 plan:

```python
class ArticleGroupingResponse(BaseModel):
    status: Literal['READY', 'UNAVAILABLE']
    generatedAt: datetime | None
    issue: ArticleGroupingIssueResponse | None
```

Each article requires `similarGroupId: str`, `isSimilarGroupRepresentative: bool`, and `exactDuplicateCount: int >= 0`.
2. Build public IDs only in the assembler: `sim-{cluster_uid}-{group_rank}`. Do not expose database group IDs.
3. For `UNAVAILABLE`, return the fixed public issue and singleton IDs in server article order. Do not expose stored/internal exception strings, and do not change the cluster response HTTP status.
4. Validate READY invariants before response construction: all returned articles have membership, one representative per group, group ranks contiguous. Raise an internal data-integrity error rather than emitting contradictory JSON.
5. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py -q
```

6. Commit:

```bash
git add app/schemas/cluster.py app/domains/clusters tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py
git commit -m "feat: 클러스터 기사 그룹 응답 제공"
```

## Task 10: Verify B4 end to end

1. Update `tests/contracts/test_openapi_read_routes.py` for grouping enums, required article fields, fixed issue shape, and required processed IDs.
2. Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py tests/batch/test_article_similarity.py tests/batch/test_group_similar_articles_step.py tests/batch/test_article_similarity_evaluation.py tests/repositories/test_article_group_repo.py tests/batch/test_build_page_snapshot_rebuild.py tests/domains/test_clusters_service.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py tests/contracts/test_openapi_read_routes.py tests/db/test_schema_migrations.py
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests scripts
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests scripts
git diff --check
```

3. With Ollama running, execute one integration batch containing: a multi-article same-event group, a hard negative, a singleton, and one forced provider failure. Confirm only the failed cluster is `UNAVAILABLE`, exact counts remain present, no vectors exist in PostgreSQL, and page status is unchanged.
4. Commit the OpenAPI assertions if not already included:

```bash
git add tests/contracts/test_openapi_read_routes.py
git commit -m "test: 유사 기사 그룹 API 계약 고정"
```
