
## Task 1 Characterization
- OpenAPI route contracts should assert both concrete path presence and method pairs; archive is intentionally `/stock/api/pages/archive`, not `/stock/api/archive`.
- Successful `ApiSuccess` responses currently omit an `error` key when the value is `None`; archive API responses currently serialize only `items` and `pagination` from the archive list response model.
- Provider fallback behavior is non-fatal for article content timeouts and market-index per-ticker failures; orchestrator-level step failures mark the job failed and re-raise.

## Task 2 DB Identifier Hardening
- Centralizing schema qualification in `app.db.identifiers` keeps raw SQL call sites consistent while validating schema and internal table/enum identifiers before string formatting.
- Quoting the validated schema in `SET search_path` preserves the current `stock` behavior and removes direct interpolation from connection setup.

## Task 3 Provider Observability
- Non-fatal provider fallbacks can stay behaviorally unchanged while exposing structured failure details on existing result/metadata surfaces: provider result objects for fetchers, batch warning events for cluster enrichment, and `metadata_json` for AI summaries.
- Structured error payloads are most useful when they keep stable keys for provider/component name, target identifier (ticker or URL), exception class, and exception message.

## Task 4 Batch Transaction Policy
- Batch startup visibility is now characterized by repository-level commits on `create_job`, so the API scheduler receives an already-durable `RUNNING` job handle.
- Failure durability is characterized as separate durable failure-event and `FAILED` status commits after failed-step domain writes have been rolled back by the failing step/session policy.

## Task 5 Batch Transaction Ownership
- Normal batch writes now rely on explicit service/orchestrator boundary commits instead of hidden repository or step commits.
- Startup durability is preserved by committing the created RUNNING job and CREATE_JOB event before the API schedules the background orchestrator.
- Failure durability is preserved by rolling back active domain work first, then committing the ORCHESTRATE error event and FAILED job status in separate orchestrator-owned commits.

## Task 6 Session Contract Cleanup
- Batch steps now fail fast on a missing repository session via a shared explicit contract instead of silently switching to scaffold/no-op behavior based on `session.bind`.
- Step constructors resolve repository/provider factories at runtime, which keeps production wiring unchanged while letting tests inject explicit fakes or keep using monkeypatch without hidden bind checks.
- Cluster grouping now depends only on the LLM provider configuration; fake-session tests that want scaffold-style grouping must express that by injecting an unconfigured provider.

## Task 7 Pages/Clusters Decoupling
- Pages and clusters services can stay HTTP-free by returning JSON-shaped domain payload dicts while routers own both FastAPI dependency factories and final `response_model` assembly.
- When replacing service-layer Pydantic returns with dict payloads, `model_dump(mode='json')` preserves the existing serialized date/datetime contract that the characterization tests expect.

## Task 8 Batch and Archive Boundaries
- Batches and archive can follow the Task 7 pages pattern by returning JSON-shaped dict payloads from services while routers own FastAPI dependency wiring, `response_model` validation, and final envelope assembly.
- To preserve existing batch datetime strings exactly, batch payload builders must serialize datetimes with `datetime.isoformat()` instead of relying on Pydantic's UTC `Z` normalization.
- Moving background scheduling to the router boundary still preserves Task 5 startup durability when the service commits the RUNNING job and CREATE_JOB event before the router adds the orchestrator task.

## Task 9 Router Metadata Ownership
- Feature routers can own their own `prefix` and `tags` without changing the published OpenAPI paths as long as the aggregator keeps only the global `/stock/api` prefix.
- The archive route stays mounted under the pages namespace at `/stock/api/pages/archive` even when the archive router carries its own `archive` tag.
