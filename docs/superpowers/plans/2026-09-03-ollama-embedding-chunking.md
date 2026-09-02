# Ollama Embedding Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add provider-level sequential Ollama embedding chunks with a default batch size of 8 while preserving the public embedding API and whole-cluster grouping fallback.

**Architecture:** Extend `Settings` with the validated chunk-size contract. Refactor `OllamaEmbeddingProvider.embed_articles` to build all inputs once, reuse one client, await contiguous chunks sequentially, validate shared vector dimension, and flatten the result. Keep `GroupSimilarArticlesStep` as the single whole-cluster grouping boundary; a final chunk error discards provider partials and uses the existing cluster-wide singleton `UNAVAILABLE` fallback.

**Tech Stack:** Python 3.14, FastAPI settings with Pydantic v2, httpx `MockTransport`, `AsyncClient`, pytest/AnyIO, existing `OllamaEmbeddingProvider`, `GroupSimilarArticlesStep`, and `log_safe_exception`.

**Spec:** `docs/superpowers/specs/2026-09-03-ollama-embedding-chunking-design.md`

## Global Constraints

- `ollama_embed_batch_size` default is `8`; environment alias is `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE`; validation is `ge=1`.
- Existing `ollama_timeout_seconds` default `30.0` and `ollama_max_retries` default `2`, range `0..2`, remain unchanged.
- Existing public `embed_articles(...) -> list[list[float]]` and `embed(...)` compatibility alias remain unchanged.
- All article inputs are built once before I/O; one `AsyncClient` serves every chunk; chunks are contiguous and sequential; results retain original order.
- Each chunk calls the existing per-request retry path; no cluster-level extra retry, timeout increase, chunk parallelism, `n_slots`, GPU, or hardware tuning is allowed.
- A final chunk failure discards partial vectors, does not call subsequent chunks, and routes the entire cluster through existing singleton `UNAVAILABLE` fallback; no partial DB persistence occurs.
- Grouping executes once over the whole cluster vector list, and vector data is never persisted.
- Cross-chunk vector dimension mismatch is a sanitized `invalid_response` failure and is not retried.
- New diagnostics contain only safe cluster/article counts, chunk index/count/size, attempt, elapsed, failure reason, and numeric HTTP status as practical; no content, vector, full endpoint, provider response, or secret is logged.
- Local/CI tests use `httpx.MockTransport`; Ollama installation and model pulls are not required.
- Any DB integration test must use disposable local PostgreSQL only; production or shared DB access is forbidden.
- No DB migration, `db/schema_postgresql.sql` change, vector persistence, algorithm version change, or `BatchLlm`/Gemini change is in scope.
- Rollback is `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE=60` followed by a batch worker restart; no data cleanup or schema rollback is required.
- Preserve unrelated work and never modify, stage, or commit the user-untracked `docs/backend-requests.md`.
- Follow TDD for implementation: write the focused test, run it to observe RED, add the smallest implementation, run GREEN, then commit the task.

---

### Task 1: Add the validated Ollama chunk-size setting

**Files:**
- Modify: `app/core/settings.py:263-277` (`Settings.ollama_timeout_seconds` through `Settings.ollama_max_retries`); add the new field immediately after `ollama_max_retries`.
- Modify: `tests/core/test_settings.py:345-398` (`test_settings_loads_ollama_configuration_from_environment`, `test_settings_uses_ollama_defaults`, and `test_settings_rejects_invalid_ollama_bounds`).

**Interfaces:**
- Consumes: existing `pydantic_settings.BaseSettings`, `Field`, and `AliasChoices` imports and the current `STOCKAPP_OLLAMA_*` alias convention.
- Produces: `Settings.ollama_embed_batch_size: int`, default `8`, accepted aliases `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE` and `ollama_embed_batch_size`, with `ge=1`.
- Does not alter: `Settings.ollama_timeout_seconds`, `Settings.ollama_max_retries`, or any database/AI setting.

- [ ] **Step 1: Write the failing settings tests.** Add the new environment value to the existing Ollama configuration test, add it to the default-clearing loop, assert the default, and add explicit positive-bound cases:

```python
def test_settings_loads_ollama_configuration_from_environment(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv('STOCKAPP_OLLAMA_BASE_URL', 'http://ollama.test:11434/')
    monkeypatch.setenv('STOCKAPP_OLLAMA_EMBED_MODEL', 'custom-embed')
    monkeypatch.setenv('STOCKAPP_OLLAMA_TIMEOUT_SECONDS', '12.5')
    monkeypatch.setenv('STOCKAPP_OLLAMA_MAX_RETRIES', '1')
    monkeypatch.setenv('STOCKAPP_OLLAMA_EMBED_BATCH_SIZE', '3')
    monkeypatch.setenv('STOCKAPP_SIMILARITY_INPUT_CHARS', '1024')

    settings = settings_module.Settings(_env_file=None)

    assert settings.ollama_base_url == 'http://ollama.test:11434/'
    assert settings.ollama_embed_model == 'custom-embed'
    assert settings.ollama_timeout_seconds == 12.5
    assert settings.ollama_max_retries == 1
    assert settings.ollama_embed_batch_size == 3
    assert settings.similarity_input_chars == 1024


def test_settings_uses_ollama_defaults(monkeypatch: pytest.MonkeyPatch):
    for name in (
        'STOCKAPP_OLLAMA_BASE_URL',
        'STOCKAPP_OLLAMA_EMBED_MODEL',
        'STOCKAPP_OLLAMA_TIMEOUT_SECONDS',
        'STOCKAPP_OLLAMA_MAX_RETRIES',
        'STOCKAPP_OLLAMA_EMBED_BATCH_SIZE',
        'STOCKAPP_SIMILARITY_INPUT_CHARS',
    ):
        monkeypatch.delenv(name, raising=False)

    settings = settings_module.Settings(_env_file=None)

    assert settings.ollama_base_url == 'http://localhost:11434'
    assert settings.ollama_embed_model == 'bge-m3'
    assert settings.ollama_timeout_seconds == 30
    assert settings.ollama_max_retries == 2
    assert settings.ollama_embed_batch_size == 8
    assert settings.similarity_input_chars == 2048


@pytest.mark.parametrize('batch_size', [0, -1])
def test_settings_rejects_non_positive_ollama_embed_batch_size(batch_size: int):
    with pytest.raises(ValidationError, match='ollama_embed_batch_size'):
        settings_module.Settings(
            _env_file=None,
            ollama_embed_batch_size=batch_size,
        )
```

- [ ] **Step 2: Run the focused tests to verify RED.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/core/test_settings.py -q -k 'ollama_embed_batch_size or uses_ollama_defaults or loads_ollama_configuration'
```

Expected: FAIL because the current `Settings` has no `ollama_embed_batch_size` attribute and ignores the new environment alias; the existing timeout/retry assertions remain unchanged.

- [ ] **Step 3: Add the minimal Pydantic field.** Insert this declaration after `ollama_max_retries` in `app/core/settings.py`:

```python
    ollama_embed_batch_size: int = Field(
        default=8,
        ge=1,
        validation_alias=AliasChoices(
            'STOCKAPP_OLLAMA_EMBED_BATCH_SIZE',
            'ollama_embed_batch_size',
        ),
    )
```

Do not change `SettingsConfigDict`, the Ollama URL validator, timeout default, or retry range.

- [ ] **Step 4: Run the focused tests to verify GREEN.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/core/test_settings.py -q -k 'ollama_embed_batch_size or uses_ollama_defaults or loads_ollama_configuration'
```

Expected: PASS, including default `8`, environment value `3`, and rejection of `0` and `-1`.

- [ ] **Step 5: Commit the setting contract.**

```bash
git add app/core/settings.py tests/core/test_settings.py
git commit -m "feat: Ollama 임베딩 청크 설정 추가"
```

### Task 2: Implement provider-level sequential chunks and cross-chunk validation

**Files:**
- Modify: `app/batch/providers/ollama_embedding_provider.py:75-91` (`OllamaEmbeddingProvider.embed_articles`); add a private `_embed_chunks` helper beside `_build_client`.
- Modify: `tests/batch/test_ollama_embedding_provider.py:59-107` (existing `MockTransport` request tests and new chunking tests).

**Interfaces:**
- Consumes: `Settings.ollama_embed_batch_size`, existing `EmbeddingArticle` input builder, `_build_client`, `_request_embeddings`, and `_parse_embeddings`.
- Produces: the unchanged public `async def embed_articles(self, articles: Sequence[EmbeddingArticle | Mapping[str, object] | object]) -> list[list[float]]` and unchanged `embed` alias.
- Private helper contract: `async def _embed_chunks(self, client: _EmbeddingHttpClient, inputs: Sequence[str]) -> list[list[float]]`, returning only a complete flattened list or raising a sanitized `OllamaEmbeddingError`.

- [ ] **Step 1: Write the failing happy-path and dimension tests.** Append these tests to the existing provider test module; `_article`, `Settings`, `httpx`, `json`, and `pytest` already exist in that file:

```python
@pytest.mark.anyio
async def test_embed_articles_sends_sequential_chunks_and_flattens_original_order():
    requests: list[dict[str, object]] = []
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        first_index = payload['input'][0].split()[1]
        events.append(f'start:{first_index}')
        vectors = [
            [float(value.split()[1]), 1.0]
            for value in payload['input']
        ]
        events.append(f'end:{first_index}')
        return httpx.Response(
            200,
            json={'embeddings': vectors},
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(5)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        build_calls = 0
        original_build_input = provider.build_input

        def counting_build_input(article):
            nonlocal build_calls
            build_calls += 1
            return original_build_input(article)

        provider.build_input = counting_build_input
        result = await provider.embed_articles(articles)

    assert [len(payload['input']) for payload in requests] == [2, 2, 1]
    assert [value[0] for value in result] == [0.0, 1.0, 2.0, 3.0, 4.0]
    assert events == [
        'start:0',
        'end:0',
        'start:2',
        'end:2',
        'start:4',
        'end:4',
    ]
    assert build_calls == len(articles)


@pytest.mark.anyio
async def test_embed_articles_rejects_dimension_change_between_chunks():
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(payload['input']) == 3:
            dimension = 2
        else:
            dimension = 2 if len(requests) == 1 else 1
        return httpx.Response(
            200,
            json={
                'embeddings': [
                    [float(index) for index in range(dimension)]
                    for _ in payload['input']
                ]
            },
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(3)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='invalid response'):
            await provider.embed_articles(articles)

    assert [len(payload['input']) for payload in requests] == [2, 1]
```

- [ ] **Step 2: Run the provider tests to verify RED.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py -q -k 'sequential_chunks or dimension_change_between_chunks'
```

Expected: FAIL because the current provider sends one five-input request instead of `[2, 2, 1]`; the current one-request dimension response succeeds rather than exercising a cross-chunk dimension change.

- [ ] **Step 3: Replace the one-request body with a complete sequential implementation.** Keep `build_input` and `_request_embeddings` behavior intact, and make `embed_articles` call this private helper while preserving empty-input behavior and client ownership:

```python
    async def embed_articles(
        self, articles: Sequence[EmbeddingArticle | Mapping[str, object] | object]
    ) -> list[list[float]]:
        """Embed all articles in sequential chunks, preserving input order."""

        inputs = [self.build_input(article) for article in articles]
        if not inputs:
            return []
        if self._client is not None:
            return await self._embed_chunks(self._client, inputs)
        async with self._build_client() as client:
            return await self._embed_chunks(client, inputs)

    async def _embed_chunks(
        self,
        client: _EmbeddingHttpClient,
        inputs: Sequence[str],
    ) -> list[list[float]]:
        batch_size = getattr(self._settings, 'ollama_embed_batch_size', 8)
        chunk_count = (len(inputs) + batch_size - 1) // batch_size
        flattened: list[list[float]] = []
        expected_dimension: int | None = None

        for offset in range(0, len(inputs), batch_size):
            chunk_inputs = inputs[offset : offset + batch_size]
            payload: Mapping[str, object] = {
                'model': self._settings.ollama_embed_model,
                'input': chunk_inputs,
                'truncate': False,
            }
            chunk_vectors = await self._request_embeddings(
                client,
                payload,
                len(chunk_inputs),
            )
            if expected_dimension is None:
                expected_dimension = len(chunk_vectors[0])
            elif any(
                len(vector) != expected_dimension for vector in chunk_vectors
            ):
                raise OllamaEmbeddingError(
                    'invalid response.',
                    reason='invalid_response',
                )
            flattened.extend(chunk_vectors)

        if len(flattened) != len(inputs) or chunk_count < 1:
            raise OllamaEmbeddingError(
                'invalid response.',
                reason='invalid_response',
            )
        return flattened
```

`_parse_embeddings` already rejects an empty vector and enforces a common dimension within one response. The helper adds only the cross-chunk check; it must not normalize values, start tasks, retry, or persist vectors. The `getattr(self._settings, 'ollama_embed_batch_size', 8)` fallback keeps existing lightweight `SimpleNamespace` test doubles valid while production `Settings` remains the source of the validated value.

- [ ] **Step 4: Run the full provider module to verify GREEN.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py -q
```

Expected: PASS, including existing request-shape, response-close, retry, cancellation, and injected-client ownership tests plus the new sequential and cross-chunk dimension tests.

- [ ] **Step 5: Commit the provider chunking unit.**

```bash
git add app/batch/providers/ollama_embedding_provider.py tests/batch/test_ollama_embedding_provider.py
git commit -m "feat: Ollama 임베딩 provider 순차 청킹"
```

### Task 3: Preserve whole-cluster fallback and add safe chunk diagnostics

**Files:**
- Modify: `app/batch/providers/ollama_embedding_provider.py:16-35,117-160` (`OllamaEmbeddingError` and `_request_embeddings`) and the new `_embed_chunks` error boundary from Task 2.
- Modify: `tests/batch/test_ollama_embedding_provider.py` (per-chunk retry budget, final-chunk stop, and redaction tests).
- Modify: `app/batch/steps/group_similar_articles.py:100-145,178-291` (`GroupSimilarArticlesStep` settings and cluster loop); keep `build_grouping_algorithm_version` unchanged.
- Modify: `tests/batch/test_group_similar_articles_step.py:113-253` (existing fakes and whole-cluster fallback tests plus final-chunk integration coverage).

**Interfaces:**
- Consumes: Task 1 `Settings.ollama_embed_batch_size`, Task 2 `_embed_chunks`, existing `_request_embeddings` retry semantics, `_embed_articles`, `replace_cluster_groups`, and `mark_grouping_unavailable_with_singletons`.
- Produces: optional numeric `OllamaEmbeddingError.attempt` metadata for internal diagnostics; the existing `reason`, `status_code`, message, public return type, and step fallback contract remain compatible.
- Produces: safe provider warning fields `article_count`, `chunk_index`, `chunk_count`, `chunk_size`, `attempt`, `elapsed_seconds`, `failure_reason`, and `status_code`; safe step target fields `cluster_id`, `cluster_count`, `article_count`, and `chunk_count`.

- [ ] **Step 1: Write failing retry, stop, fallback, and redaction tests.** Put the first two tests in `tests/batch/test_ollama_embedding_provider.py` and the step test in `tests/batch/test_group_similar_articles_step.py`. They use only `httpx.MockTransport`, the current `_article`, `_repositories`, `_step`, `FakeBatchRepository`, and `RecordingGroupRepository` helpers:

```python
@pytest.mark.anyio
async def test_embed_articles_applies_retry_budget_to_each_chunk():
    attempts_by_input: dict[str, int] = {}
    request_sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        request_sizes.append(len(payload['input']))
        key = payload['input'][0]
        attempts_by_input[key] = attempts_by_input.get(key, 0) + 1
        if attempts_by_input[key] < 3:
            return httpx.Response(503, json={'error': 'busy'}, request=request)
        return httpx.Response(
            200,
            json={'embeddings': [[1.0, 0.0] for _ in payload['input']]},
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=2,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(4)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        result = await provider.embed_articles(articles)

    assert result == [[1.0, 0.0]] * 4
    assert request_sizes == [2, 2, 2, 2, 2, 2]
    assert attempts_by_input == {
        'article 0 summary': 3,
        'article 2 summary': 3,
    }


@pytest.mark.anyio
async def test_final_chunk_failure_stops_later_chunks_and_returns_no_partial_vectors():
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            return httpx.Response(
                503,
                content=b'provider secret-token response body',
                request=request,
            )
        return httpx.Response(
            200,
            json={'embeddings': [[1.0, 0.0] for _ in payload['input']]},
            request=request,
        )

    settings = Settings(
        _env_file=None,
        ollama_base_url='http://ollama.test',
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
    )
    articles = [_article(f'article {index}', 'summary') for index in range(5)]

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        with pytest.raises(OllamaEmbeddingError, match='request failed'):
            await provider.embed_articles(articles)

    assert [len(payload['input']) for payload in requests] == [2, 2]
    assert [value.split()[1] for value in requests[-1]['input']] == ['2', '3']


@pytest.mark.anyio
async def test_final_chunk_failure_uses_cluster_singletons_without_ready_write(
    caplog,
):
    cluster_repo, group_repo = _repositories((1,))
    cluster_repo.memberships[1].append(
        {'processed_article_id': 13, 'article_rank': 3}
    )
    cluster_repo.articles[13] = _article(13)
    cluster_repo.counts.update({11: 6, 12: 7, 13: 8})
    requests: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 2:
            return httpx.Response(
                503,
                content=b'https://secret.example token=secret-token',
                request=request,
            )
        return httpx.Response(
            200,
            json={'embeddings': [[1.0, 0.0] for _ in payload['input']]},
            request=request,
        )

    settings = SimpleNamespace(
        ollama_base_url='http://ollama.test',
        ollama_embed_model='bge-m3',
        ollama_timeout_seconds=1.0,
        ollama_max_retries=0,
        ollama_embed_batch_size=2,
        similarity_input_chars=2048,
    )
    caplog.set_level(
        logging.WARNING,
        logger='app.batch.providers.ollama_embedding_provider',
    )
    caplog.set_level(
        logging.INFO,
        logger='app.batch.steps.group_similar_articles',
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        context = await _step(
            cluster_repo,
            group_repo,
            provider,
            settings=settings,
        ).run(FakeBatchRepository(), _context())

    assert [len(payload['input']) for payload in requests] == [2, 1]
    assert group_repo.ready == []
    assert [row[0] for row in group_repo.unavailable] == [1]
    assert [
        row['processed_article_id'] for row in group_repo.unavailable[0][1]
    ] == [11, 12, 13]
    assert context.partial_categories == {SIMILARITY_GROUPING_FAILED: 1}

    provider_log = next(
        record
        for record in caplog.records
        if 'Ollama embedding chunk failed' in record.getMessage()
    )
    provider_text = provider_log.getMessage()
    assert 'article_count=3' in provider_text
    assert 'chunk_index=2' in provider_text
    assert 'chunk_count=2' in provider_text
    assert 'chunk_size=1' in provider_text
    assert 'attempt=1' in provider_text
    assert 'failure_reason=http_status' in provider_text
    assert 'status_code=503' in provider_text
    assert 'elapsed_seconds=' in provider_text
    step_log = next(
        record
        for record in caplog.records
        if 'Similar article grouping embedding target' in record.getMessage()
    )
    assert 'cluster_id=1' in step_log.getMessage()
    assert 'cluster_count=1' in step_log.getMessage()
    assert 'article_count=3' in step_log.getMessage()
    assert 'chunk_count=2' in step_log.getMessage()
    assert 'secret.example' not in caplog.text
    assert 'secret-token' not in caplog.text
```

- [ ] **Step 2: Run the focused failure tests to verify RED.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py tests/batch/test_group_similar_articles_step.py -q -k 'each_chunk or final_chunk or safe_numeric'
```

Expected: FAIL because the current provider gives one retry sequence to one whole request, returns/continues according to the old one-request boundary, and emits no chunk diagnostics; the step test therefore sees a ready write instead of `[2, 1]` requests followed by all-cluster singleton fallback.

- [ ] **Step 3: Add attempt metadata and safe provider diagnostics without changing retry decisions.** Import `logging` and `perf_counter`, define a module logger and a closed reason allowlist, and extend the sanitized error with an optional integer attempt:

```python
LOGGER = logging.getLogger(__name__)
_SAFE_FAILURE_REASONS = frozenset(
    {
        'unknown',
        'invalid_input',
        'timeout',
        'network_error',
        'http_status',
        'invalid_response',
        'retries_exhausted',
    }
)


class OllamaEmbeddingError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        reason: str = 'unknown',
        status_code: int | None = None,
        attempt: int | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.attempt = attempt


def _safe_failure_reason(value: object) -> str:
    if isinstance(value, str) and value in _SAFE_FAILURE_REASONS:
        return value
    return 'unknown'


def _safe_status_code(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
```

Pass `attempt=attempt + 1` to every terminal `_request_embeddings` error. Around the existing `_parse_embeddings` call, set the same numeric attempt on a sanitized error before re-raising:

```python
                try:
                    return self._parse_embeddings(response, expected_count)
                except OllamaEmbeddingError as exc:
                    exc.attempt = attempt + 1
                    raise
```

In `_embed_chunks`, measure only the chunk call and log only the allowlisted fields before re-raising; the partial `flattened` list stays local and is never returned:

```python
            started_at = perf_counter()
            try:
                chunk_vectors = await self._request_embeddings(
                    client,
                    payload,
                    len(chunk_inputs),
                )
            except OllamaEmbeddingError as exc:
                LOGGER.warning(
                    'Ollama embedding chunk failed article_count=%d '
                    'chunk_index=%d chunk_count=%d chunk_size=%d attempt=%s '
                    'elapsed_seconds=%.3f failure_reason=%s status_code=%s',
                    len(inputs),
                    offset // batch_size + 1,
                    chunk_count,
                    len(chunk_inputs),
                    exc.attempt,
                    perf_counter() - started_at,
                    _safe_failure_reason(exc.reason),
                    _safe_status_code(exc.status_code),
                )
                raise
```

Do not log `str(exc)`, `request.url`, payload, response body, or vector values. Keep existing retry condition branches, timeout, response close, and cancellation behavior unchanged.

- [ ] **Step 4: Add step-level numeric target logging and retain the existing fallback boundary.** Resolve the setting with the same test-double-safe default and do not include it in algorithm version:

```python
        self._embed_batch_size = _positive_int(
            getattr(resolved_settings, 'ollama_embed_batch_size', 8),
            field='ollama_embed_batch_size',
        )
```

Immediately before the existing `try` around `_embed_articles` in `run`, emit only numeric target context:

```python
            chunk_count = (
                len(target['articles']) + self._embed_batch_size - 1
            ) // self._embed_batch_size
            LOGGER.info(
                'Similar article grouping embedding target cluster_id=%d '
                'cluster_count=%d article_count=%d chunk_count=%d',
                cluster_id,
                len(ordered_clusters),
                len(target['articles']),
                chunk_count,
            )
```

Leave the existing `vectors = await _embed_articles(...)`, full-count check, single `group_similar_articles(...)` call, `replace_cluster_groups`, `log_safe_exception`, `repository.add_event`, singleton fallback, and `progress.commit_target` order intact. Do not add a chunk checkpoint or call grouping inside the provider loop. Keep existing model/reason/status event fields sanitized as they are today.

- [ ] **Step 5: Run the provider and step tests to verify GREEN.**

Run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/batch/test_ollama_embedding_provider.py tests/batch/test_group_similar_articles_step.py -q
```

Expected: PASS, including each-chunk retry isolation, final-chunk stop, no-ready-write singleton fallback, whole-cluster grouping, response/client cleanup, cancellation propagation, and redaction assertions.

- [ ] **Step 6: Commit the failure-boundary and diagnostics unit.**

```bash
git add app/batch/providers/ollama_embedding_provider.py tests/batch/test_ollama_embedding_provider.py app/batch/steps/group_similar_articles.py tests/batch/test_group_similar_articles_step.py
git commit -m "feat: Ollama 청크 실패 진단과 클러스터 fallback 유지"
```

## Final verification handoff

After all three task commits, the implementing agent runs the focused commands again
without `-k`, then performs documentation and repository checks:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/core/test_settings.py tests/batch/test_ollama_embedding_provider.py tests/batch/test_group_similar_articles_step.py -q
UV_CACHE_DIR=/tmp/uv-cache uv run ruff format --check app tests
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check app tests
git diff --check
```

No Ollama installation or production DB is part of this handoff. If a local DB
regression is separately enabled, verify its DSN is disposable local PostgreSQL and
record that constraint in the test report. To roll back operationally, set
`STOCKAPP_OLLAMA_EMBED_BATCH_SIZE=60` and restart the batch worker.
