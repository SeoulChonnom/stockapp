
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
