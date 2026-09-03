# Ollama embedding chunking final fix report

- Fix base: `c740d459bcf7c0ced3afe4b336df2e8a5c0ac26b`
- Branch: `codex/ollama-embedding-chunking`
- Fix-wave commit subject: `test: Ollama 청킹 회귀 검증 보강`
- Scope: provider diagnostics and regression coverage only; `docs/backend-requests.md` was pre-existing untracked work and was not modified, staged, or committed.

## Findings addressed

1. Cross-chunk dimension mismatch now enters the existing provider warning boundary. It raises sanitized `invalid_response` with numeric `attempt=1`, logs the current `article_count`, `chunk_index`, `chunk_count`, `chunk_size`, and `elapsed_seconds`, and records `status_code=None`. The warning contains no article input, vector, endpoint, response body, or secret.
2. Added an all-503 `ollama_max_retries=2` regression asserting three requests and terminal `OllamaEmbeddingError.attempt == 3`.
3. Strengthened the default chunk regression to 17 inputs without an explicit batch size, asserting request sizes `[8, 8, 1]`, original result order, sequential start/end events, and one `build_input` call per article.
4. Updated only the corresponding plan test snippet and RED expectation to describe the approved default-8 regression.

## TDD evidence

The focused diagnostic test was extended before the production change:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py -q -k 'default_chunks or logs_dimension_change_between_chunks or all_503'
1 failed, 2 passed, 37 deselected
Failure: expected one provider warning, found zero records.
```

After moving dimension validation into the existing safe warning boundary and assigning the closed numeric attempt metadata:

```text
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py -q -k 'default_chunks or logs_dimension_change_between_chunks or all_503'
3 passed, 37 deselected
```

## Verification evidence

```text
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py tests/batch/test_group_similar_articles_step.py -q
54 passed, 2 warnings

UV_CACHE_DIR=/tmp/uv-cache uv run pytest
1328 passed, 26 skipped, 1 warning

UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app/batch/providers/ollama_embedding_provider.py tests/batch/test_ollama_embedding_provider.py
2 files already formatted

UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/batch/providers/ollama_embedding_provider.py tests/batch/test_ollama_embedding_provider.py
All checks passed!

git diff --check
clean (exit 0)
```

No Ollama, external network, MCP, production DB, migration, schema, algorithm-version,
Group fallback, cancellation, or LLM behavior was changed. The full suite's skipped
tests are the repository's existing PostgreSQL/live integration skips; no DB test was
enabled for this fix wave.
