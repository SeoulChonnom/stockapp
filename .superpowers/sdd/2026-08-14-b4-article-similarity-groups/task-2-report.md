# B4 Task 2 report: Ollama embedding provider

## Changes

- Added `Settings` fields and environment aliases for the Ollama base URL,
  embedding model, timeout, retry count, and deterministic input character cap.
- Added `OllamaEmbeddingProvider` with one `/api/embed` batch request per
  non-empty article cluster, `bge-m3`/`truncate=false` payloads, and ordered
  vector results.
- Article inputs use the shared `app.core.text.normalize_text` helper (NFC,
  case-folding, and whitespace collapse), combining canonical title with
  source summary or falling back to body excerpt before applying the cap.
- Added strict response validation for HTTP/JSON shape, count, shared
  non-zero dimensions, numeric non-boolean finite values, without vector
  normalization.
- Retries are limited to network/timeout failures and HTTP 408/429/5xx, with
  injected httpx clients and sleep functions for deterministic tests. Injected
  clients remain caller-owned; internally created clients are closed.
- Public errors use sanitized fixed messages and cancellation propagates.

## TDD and verification

- RED observed first: provider test collection failed with the expected
  missing-module error before implementation.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/core/test_settings.py tests/batch/test_ollama_embedding_provider.py -q`
  -> 85 passed (only the repository's existing dependency deprecation warnings).
- `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app/core/settings.py app/batch/providers/ollama_embedding_provider.py app/batch/providers/__init__.py tests/core/test_settings.py tests/batch/test_ollama_embedding_provider.py`
  -> all checks passed.
- `UV_CACHE_DIR=/tmp/uv-cache uv run pyright app/core/settings.py app/batch/providers/ollama_embedding_provider.py`
  -> 0 errors, 0 warnings.
- `git diff --check` -> clean.

The review-evidence rerun above is scoped to settings and the Ollama provider;
the earlier full relevant run covered `tests/core tests/batch` with 573 passed
before the additional cleanup-only tests were added.

All provider tests use `httpx.MockTransport`; no Ollama or Gemini endpoint was
called.

## Review follow-up

- Validated and whitespace-normalized `ollama_base_url` as an HTTP(S) URL with
  a host, rejecting unsupported schemes, blank values, malformed hosts, query
  strings, fragments, credentials, and invalid ports.
- Every received HTTP response is awaited-closed on success, retry, permanent
  status, and invalid JSON paths. Injected clients remain open while
  internally-owned clients are closed by the provider context manager.
- Restricted success to 2xx and sanitized `float()` type/value/overflow errors;
  non-unit vectors remain unchanged.
- Added MockTransport coverage for timeout, 408/429/5xx retry counts, 1xx/3xx
  rejection, response cleanup, ownership, URL validation, and huge integer
  overflow.

## Residual risks

- The provider is intentionally limited to transport/validation; grouping,
  scoring, and persistence remain later B4 tasks.
- The 2048-character cap is the approved deterministic approximation for the
  512-token input limit and is recorded by later algorithm-version work.
- Existing `docs/backend-requests.md` remains untouched and untracked.
