# Task 10 B4 verification report

Date: 2026-08-14 (Asia/Seoul)

## Contract/evidence changes

- `tests/contracts/test_openapi_read_routes.py` now fixes the public read-route
  response refs and the required grouping/article fields. It asserts
  `READY|UNAVAILABLE`, required `status/generatedAt/issue`, the fixed issue
  code/message shape, and `exactDuplicateCount` minimum `0`.
- `app/domains/pages/assembler.py` now consumes persisted snapshot grouping
  fields (`similar_group_rank`, representative flag, exact duplicate count) and
  rejects missing/invalid values. It no longer fabricates grouping metadata at
  read time.
- `tests/integration/test_article_grouping_persisted_flow.py` runs the actual
  grouping step through `OllamaEmbeddingProvider` and an in-process
  `httpx.MockTransport`: same-event pair, direction hard-negative, singleton,
  and forced provider failure in another cluster. It verifies cluster-local
  `UNAVAILABLE`, exact counts, unchanged page `READY` status, persisted public
  cluster/page payloads, and no vector fields/columns.

Effective grouping version in the evidence test:

`format=similarity-v2;model=bge-m3;inputChars=2048;lexical=lexical-v1;weights=0x1.999999999999ap-3,0x1.3333333333333p-2,0x1.3333333333333p-2,0x1.999999999999ap-3,0x1.3333333333333p-1,0x1.999999999999ap-2;threshold=0x1.ccccccccccccdp-2;veto=veto-v1;grouping=complete-link-v1`

## Commands and results

| Command | Result |
| --- | --- |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/contracts/test_openapi_read_routes.py tests/batch/test_group_similar_articles_step.py tests/domains/test_cluster_assembler.py tests/api/test_clusters.py tests/repositories/test_article_group_repo.py tests/repositories/test_page_snapshot_write_repo.py -q` | 220 passed, 1 warning |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/contracts/test_openapi_read_routes.py tests/domains/test_page_assembler.py tests/integration/test_article_grouping_persisted_flow.py -q` | 62 passed, 1 warning |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q` | 1,219 passed, 24 skipped, 1 warning |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/db/test_migrations_postgresql.py -q` with ephemeral PostgreSQL 17 | 22 passed, 2 warnings |
| `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check .` | clean |
| `UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check .` | 229 files already formatted |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pyright app/domains/pages/assembler.py` | 0 errors |
| `UV_CACHE_DIR=/tmp/uv-cache uv run pyright` | 12 pre-existing errors in `app/batch/ai_output_contracts.py` and `app/domains/pages/router.py` |
| `UV_CACHE_DIR=/tmp/uv-cache uv run vulture` | 2 pre-existing findings: unused `CursorResult` import and existing `required` test variable |

The full suite skips the repository's external/live tests when their required
services or environment are absent (`24 skipped`). No live Ollama request was
made.

## PostgreSQL cleanup

Started `postgres:17` as `stockapp-task10-postgres` on `127.0.0.1:55432`, ran
the migration/repository test module, and removed it with an EXIT trap. A final
`docker ps -a --filter name=stockapp-task10-postgres` returned no rows.

## Calibration boundary

Task 7 remains **`MOCK_PIPELINE_PASS_REAL_BGE_M3_CALIBRATION_REQUIRED`**. This
Task 10 evidence proves provider/request, grouping, persistence, and public
contract behavior with deterministic MockTransport vectors only; it makes no
claim about real Ollama `bge-m3` quality, calibration metrics, model digest, or
production latency. A separately opted-in real-model calibration run remains
required before production-quality approval.
