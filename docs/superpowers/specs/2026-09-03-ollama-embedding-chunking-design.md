# Ollama 임베딩 청킹 설계

- 작성일: 2026-09-03
- 대상 저장소: `stockapp`
- 대상 브랜치: `codex/ollama-embedding-chunking`
- 상태: 사용자 승인 완료
- 기준 문서: 이 문서는 Ollama 임베딩 청킹 동작의 authoritative spec이다.

이 설계는 현재 한 번에 전송하는 클러스터 임베딩 요청을 provider 내부의 순차
청크 요청으로 바꾸는 계약을 고정한다. 구현 계획은 이 문서의 값, 호출 경계,
실패 경계, 보안·운영 제약을 코드와 테스트로 옮긴다. 두 문서 사이에 차이가
발견되면 구현 전에 이 설계를 기준으로 동기화한다.

## 1. 문제와 관측 근거

현재 provider는 `OllamaEmbeddingProvider.embed_articles`가 모든 article input을
한 배열로 만든 뒤 Ollama `/api/embed`에 한 요청으로 전송한다.

실제 `job1396`에서 관측된 성공 입력 수는 `13`, `14`, `14`였고, 관측 timeout은
`18`~`60`초 구간이었다. 현재 설정은 `ollama_timeout_seconds=30`초,
`ollama_max_retries=2`이므로 한 요청마다 초기 시도 1회와 재시도 2회를 합쳐
최대 3회가 실행된다. 해당 실행의 관측 총 소요 시간은 `32m49s`였다.

이 수치는 모델·호스트의 성능을 새로 추정하는 자료가 아니다. 다만 한 큰
요청이 timeout과 재시도 경계에 걸릴 때 클러스터 전체가 실패하고, 성공한
앞부분만 안전하게 보존할 수 없는 현재 요청 경계를 보여주는 운영 근거다.

## 2. 목표와 범위

### 2.1 목표

- provider 수준에서 입력을 최대 8개씩 연속된 순서로 나눈다.
- 한 public call에서 입력을 한 번만 build하고, 한 `AsyncClient`를 모든 청크가
  공유하게 한다.
- 각 청크가 기존 per-request retry 정책을 독립적으로 사용하게 한다.
- 청크 결과를 원래 article 순서로 평탄화하고, 청크 사이의 vector dimension도
  검증한다.
- 어느 청크가 최종 실패하면 부분 vector를 버리고 이후 청크를 호출하지 않는다.
- 기존 `list[list[float]]` public return type과 `GroupSimilarArticlesStep`의
  클러스터 단위 fallback을 유지한다.
- 그룹화는 청크별이 아니라 전체 클러스터 vector를 받은 뒤 정확히 한 번만
  실행하게 한다.
- 운영 진단에는 안전한 숫자와 닫힌 failure reason만 남기고 기사 내용·vector·
  endpoint·provider response·secret을 남기지 않는다.

### 2.2 범위 밖

다음 변경은 이 작업에 포함하지 않는다.

- DB migration, `db/schema_postgresql.sql` 변경, 새로운 vector 컬럼 또는 pgvector
- chunk parallelism, `n_slots`, GPU/hardware tuning
- timeout 증가, 기존 `ollama_max_retries=2` 상한 변경, cluster-level extra retry
- vector persistence, vector cache, embedding 저장소 추가
- grouping algorithm 또는 algorithm version 변경
- `BatchLlm`/Gemini provider와 그 retry·prompt 계약 변경
- API response schema, snapshot schema, public issue 문구 변경
- production deployment 또는 production database 접속

## 3. 현재 코드 계약과 변경 지점

### 3.1 설정

`app/core/settings.py:253-277`의 Ollama 설정은 현재 다음 필드를 제공한다.

- `ollama_base_url`: 기본 `http://localhost:11434`
- `ollama_embed_model`: 기본 `bge-m3`
- `ollama_timeout_seconds`: 기본 `30.0`, `>0`
- `ollama_max_retries`: 기본 `2`, `0..2`

새 필드는 `ollama_max_retries` 뒤에 추가한다.

| 필드 | 타입 | 기본값 | 환경 alias | 검증 |
|---|---|---:|---|---|
| `ollama_embed_batch_size` | `int` | `8` | `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE`, `ollama_embed_batch_size` | `ge=1` |

Pydantic 선언은 다음 형태를 사용한다.

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

`STOCKAPP_OLLAMA_TIMEOUT_SECONDS`와 `STOCKAPP_OLLAMA_MAX_RETRIES`의 기본값,
범위, alias는 바꾸지 않는다. `Settings(_env_file=None)`로 환경 파일 없이도
환경 alias와 기본값을 검증할 수 있어야 한다.

### 3.2 embedding provider

`app/batch/providers/ollama_embedding_provider.py`의 현재 public 계약은 다음과
같다.

```python
async def embed_articles(
    self,
    articles: Sequence[EmbeddingArticle | Mapping[str, object] | object],
) -> list[list[float]]:
    """Return one vector for each input article, in input order."""
```

`embed`는 이 메서드의 compatibility alias로 유지한다. `EmbeddingArticle`,
`OllamaEmbeddingError`, `_build_client`, `_request_embeddings`, `_parse_embeddings`
의 목적과 error reason 계약도 유지한다.

현재 호출 경계는 다음과 같다.

- `embed_articles`: `:75-91`, input build와 client lifetime의 소유자
- `build_input`: `:100-112`, normalize 및 `similarity_input_chars` cap 담당
- `_build_client`: `:114-115`, `ollama_timeout_seconds`를 가진 client 생성
- `_request_embeddings`: `:117-160`, 한 HTTP request의 retry와 response close 담당
- `_parse_embeddings`: `:169-216`, count, chunk 내부 dimension, 숫자, finite 검증

새 청킹 코드는 `embed_articles` 안에서만 public 입력을 다루며, 필요하면 private
`_embed_chunks` helper를 추가한다. 호출자는 청크의 존재를 알지 못한다.

### 3.3 grouping step

`app/batch/steps/group_similar_articles.py`의 현재 흐름은 다음과 같다.

1. `GroupSimilarArticlesStep.run` `:147-291`이 business date의 cluster를 순회한다.
2. `_load_target` `:301-348`이 article ID 순서, article row, exact count,
   `EmbeddingArticle` 배열을 만든다.
3. `_embed_articles` `:350-361`이 provider의 `embed_articles` 또는 `embed`를
   한 번 호출한다.
4. `ArticleCandidate` 전체를 만든 뒤 `group_similar_articles`를 한 번 호출한다.
5. 성공하면 `replace_cluster_groups`를 호출하고, `_EXPECTED_CLUSTER_ERRORS`에
   해당하는 provider/입력/유효성 오류면 `mark_grouping_unavailable_with_singletons`
   를 호출한다.

청킹 후에도 2~4의 단위는 변하지 않는다. provider가 여러 HTTP 응답을 모두
검증해 반환한 전체 vector에 대해서만 후보 생성과 grouping을 한 번 수행한다.

## 4. 순차 청킹 실행 계약

### 4.1 입력 build와 빈 입력

`embed_articles`는 첫 I/O 전에 다음을 정확히 한 번 실행한다.

```python
inputs = [self.build_input(article) for article in articles]
```

따라서 article 수가 `n`이면 `build_input` 호출도 `n`회이고, 청크마다 다시
normalize하거나 cap하지 않는다. `inputs`가 비어 있으면 `[]`를 반환하고
client를 만들거나 HTTP 요청을 보내지 않는다.

### 4.2 client lifetime

- injected `client`가 있으면 그 동일한 객체를 모든 청크의 `_request_embeddings`
  호출에 전달한다. provider는 injected client를 닫지 않는다.
- injected client가 없으면 `self._build_client()`를 한 번만 호출하고, 모든
  청크 요청을 하나의 `async with` 범위에서 처리한 뒤 provider가 소유한 client를
  닫는다.
- 청크 사이에 client를 새로 만들거나 닫지 않는다.
- client의 timeout은 계속 `ollama_timeout_seconds` 값이며, chunking 때문에
  timeout 값을 늘리거나 별도 timeout을 만들지 않는다.

### 4.3 chunk partition

입력은 연속된 slice로 나눈다.

```text
chunk_count = ceil(len(inputs) / ollama_embed_batch_size)
chunk 1 = inputs[0:batch_size]
chunk 2 = inputs[batch_size:2*batch_size]
chunk 3 = inputs[2*batch_size:3*batch_size]
last chunk size <= batch_size
```

각 payload는 현재 payload와 동일한 구조를 유지한다.

```json
{
  "model": "bge-m3",
  "input": ["article input text"],
  "truncate": false
}
```

청크는 `for` loop에서 `await`로 순차 실행한다. `asyncio.gather`, task 생성,
semaphore, 재정렬 작업을 사용하지 않는다. 다음 청크는 이전 청크의 request와
response close가 끝난 뒤에만 시작한다.

### 4.4 retry 경계

각 청크는 기존 `_request_embeddings`를 그대로 한 번 호출한다. 한 청크의
attempt 수는 항상 다음과 같다.

```text
attempts = ollama_max_retries + 1
```

초기 요청 1회와 최대 재시도 2회라는 현재 정책은 유지한다.

- retry 대상: `httpx.NetworkError`, `httpx.TimeoutException`, HTTP `408`, `429`,
  `5xx`
- retry하지 않음: HTTP 기타 `4xx`, invalid JSON/shape, count mismatch, chunk 내부
  dimension mismatch, non-numeric 값, NaN, infinity
- `asyncio.CancelledError`는 retry 또는 fallback하지 않고 전파한다.
- 한 청크의 retry가 성공하면 다음 청크로 넘어간다.
- 한 청크가 최종 실패하면 cluster-level extra retry를 하지 않는다.

### 4.5 result order와 dimension

청크 response의 vector들은 response input과 같은 순서라고 간주한다. 결과는
다음처럼 순서 보존으로 평탄화한다.

```text
flattened = vectors_from_chunk_1 + vectors_from_chunk_2 + vectors_from_last_chunk
```

기존 `_parse_embeddings`가 각 청크 내부의 non-empty 공통 dimension을 검증한다.
provider는 첫 청크의 dimension을 기준으로 삼고 이후 청크의 모든 vector 길이가
그 dimension과 같은지 추가 검증한다. 불일치하면 sanitized
`OllamaEmbeddingError(reason='invalid_response')`를 발생시킨다. 이 검증은
retry 대상이 아니며, 문제가 확인된 뒤 다음 청크를 호출하지 않는다.

반환 조건은 `len(flattened) == len(articles)`이며, public return type은 계속
`list[list[float]]`이다. vector를 normalize하거나 DB용 tuple로 바꾸는 일은
provider가 하지 않는다.

### 4.6 실패 원자성

누적 vector는 `embed_articles` 호출의 로컬 메모리에만 존재한다. 청크 `i`가
최종 실패하면 다음을 모두 보장한다.

- 호출자는 partial vector를 받지 않고 예외만 받는다.
- `i+1` 이후의 HTTP chunk는 호출하지 않는다.
- 성공 청크의 vector도 반환·persist·cache하지 않고 폐기한다.
- provider가 소유한 client는 예외 경로에서도 `async with`로 닫힌다.
- injected client는 예외 경로에서도 provider가 닫지 않는다.

## 5. GroupSimilarArticlesStep 동작

### 5.1 성공 경로

각 cluster마다 `_load_target`이 만든 전체 `target['embedding_articles']`를
`_embed_articles`에 한 번 전달한다. provider 내부 HTTP 호출 수가 여러 번이어도
step에서 보이는 provider 호출은 한 번이다.

전체 vector count와 article count가 일치하면 현재 코드처럼 전체 후보를 만들고
`group_similar_articles`를 다음 인자로 정확히 한 번 호출한다.

```python
group_similar_articles(
    candidates,
    threshold=self._threshold,
    parameters=self._parameters,
)
```

grouping은 청크별로 실행하지 않으며, pair score나 group order가 청크 경계의
영향을 받지 않는다.

`replace_cluster_groups`는 grouping이 완전히 성공한 뒤에만 호출한다. vector는
저장소 호출 인자나 DB row에 넣지 않는다.

### 5.2 최종 청크 실패 경로

provider의 최종 청크 실패는 기존 `_EXPECTED_CLUSTER_ERRORS` 경로로 step에
도달한다.

- 실패한 cluster에 대해 `mark_grouping_unavailable_with_singletons`를 한 번
  호출한다.
- singleton은 `target['article_ids']` 전체를 원래 cluster 순서로 포함한다.
- 실패 cluster에는 `replace_cluster_groups`를 호출하지 않는다.
- 이미 성공한 청크만으로 ready grouping을 만들거나 저장하지 않는다.
- exact duplicate count는 embedding과 독립적이므로 existing `target['exact_counts']`
  를 fallback에 그대로 전달한다.
- 이후 cluster는 기존처럼 계속 처리한다.
- `GroupSimilarArticlesStep`의 기존 public fallback 상태와 page-level partial
  처리 계약은 바꾸지 않는다.

`CancelledError`, lease loss, database write error는 기존처럼 fallback으로
삼키지 않고 전파한다. 이 작업은 repository transaction이나 migration을
추가하지 않는다.

### 5.3 algorithm version과 checkpoint

`build_grouping_algorithm_version`의 입력과 결과 문자열은 변경하지 않는다.
`ollama_embed_batch_size`는 실행 batching detail이며 algorithm semantics가
아니므로 algorithm version에 포함하지 않는다. 따라서 batch size 변경이 이미
완료된 grouping을 불필요하게 재계산하게 만들지 않는다.

현재 `target_key`와 complete-membership checkpoint 판단도 유지한다. 청크 중간
checkpoint를 만들지 않는다. cluster 결과는 전체 성공 또는 singleton fallback이
끝난 뒤 기존 target checkpoint 경계에서만 commit된다.

## 6. 안전한 진단과 보안

### 6.1 허용 필드

새로 추가하는 diagnostic record는 다음 allowlist만 사용한다.

| 위치 | 필드 | 값 규칙 |
|---|---|---|
| grouping step | `cluster_id`, `cluster_count` | 정수 |
| grouping step/provider | `article_count`, `chunk_count` | 양의 정수 또는 빈 입력의 0 |
| provider failure | `chunk_index` | 1부터 시작하는 정수 |
| provider failure | `chunk_size` | 양의 정수 |
| provider failure | `attempt` | 1부터 시작하는 정수 |
| provider failure | `elapsed_seconds` | `perf_counter` 차이를 소수 3자리로 기록 |
| provider failure | `failure_reason` | provider의 닫힌 reason 집합만 허용 |
| provider failure | `status_code` | HTTP 상태를 나타내는 정수 또는 `null` |

현재 `log_safe_exception`과 batch event의 `clusterId`, configured model,
`failureReason`, `providerStatus` 필드는 기존 계약을 유지한다. 새 chunk log는
configured model이나 URL을 넣지 않고 위 숫자와 닫힌 reason만 기록한다.

`OllamaEmbeddingError`의 공개 문자열은 계속 `request failed.`, `invalid response.`
같은 sanitized message를 사용한다. 진단을 위해 attempt를 보관하더라도 숫자
속성으로만 보관하고 exception message에 붙이지 않는다.

### 6.2 금지 필드와 redaction test

다음 값은 log, batch event, public response, exception message에 포함하지 않는다.

- article title, summary, body excerpt, normalized input, URL 또는 full endpoint
- embedding vector 전체 또는 vector 일부
- Ollama response body, 모델 파일 경로, traceback message의 provider payload
- API key, token, DSN, password, 환경변수 원문
- provider가 보내온 자유 형식 error string

`tests/batch/test_ollama_embedding_provider.py`와
`tests/batch/test_group_similar_articles_step.py`는 secret처럼 보이는 response
body와 exception message를 넣고, 허용 숫자·reason은 남으며 금지 문자열은
`caplog.text`와 event payload에 없음을 확인한다.

## 7. 저장소와 데이터 경계

- 이 변경에는 DB migration이 없다.
- `db/schema_postgresql.sql`은 수정하지 않는다.
- embedding vector는 DB, batch checkpoint, event JSON, snapshot에 저장하지 않는다.
- ready grouping persistence는 기존 `replace_cluster_groups` 호출 한 번으로만
  발생하고, provider partial result는 그 호출에 도달하지 않는다.
- unavailable singleton fallback은 기존 repository contract가 쓰는 정상 결과다.
  rollback을 위해 임의로 fallback row를 삭제하는 cleanup은 수행하지 않는다.
- API read path는 이 변경의 대상이 아니며 Ollama를 호출하지 않는다.

## 8. 테스트 및 환경 제약

### 8.1 Ollama 없는 자동화 테스트

local/CI의 provider와 step 테스트는 모두 `httpx.MockTransport`를 사용한다.
Ollama binary 설치, `bge-m3` pull, 외부 network access는 필요하지 않다.

최소 테스트 범위는 다음과 같다.

- Settings default `8`, env alias, `ge=1` rejection
- 13개 입력이 8개/5개 request로 순차 전송되고 원래 순서로 flatten
- input build가 article당 한 번이고 동일 client가 모든 chunk를 사용
- empty input의 no-request
- 각 chunk의 기존 retry budget, timeout/status retry 대상, cancellation 전파
- cross-chunk dimension mismatch
- final chunk failure 이후 no later request와 partial result discard
- GroupSimilarArticlesStep의 one provider call/one whole-cluster grouping
- 실패 cluster의 singleton `UNAVAILABLE` fallback과 ready write 부재
- safe numeric diagnostics와 content/vector/secret redaction

### 8.2 DB 경계

이 변경에서 DB 테스트는 필요하지 않다. 다른 회귀 테스트가 DB를 사용할 경우
`STOCKAPP_MIGRATION_TEST_DSN`은 disposable local PostgreSQL만 가리켜야 한다.
production 또는 shared database DSN을 설정·조회·변경하지 않는다. local DB
fixture는 명시적으로 seed한 row만 정리하고 session/transaction을 닫는다.

### 8.3 코드 품질

구현자는 TDD 순서로 각 focused test의 RED를 관찰한 뒤 최소 코드를 넣고 GREEN을
확인한다. 이 설계 작업 자체에서는 production code나 pytest를 실행하지 않는다.

## 9. 배포, rollback, cleanup

기본 rollout은 `ollama_embed_batch_size=8`로 worker를 재시작해 설정을 새로
읽게 하는 방식이다. 모니터링은 cluster/article/chunk 수, 실패 reason,
attempt, elapsed 같은 allowlisted numeric diagnostics만 사용한다.

청킹으로 provider timeout이 악화되거나 compatibility 문제가 발견되면 rollback은
다음 두 조치로 제한한다.

1. `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE=60`으로 설정한다.
2. batch worker를 재시작한다.

rollback 시 DB schema 변경이나 vector cleanup은 없다. 이 작업은 migration이나
vector persistence를 만들지 않으므로 되돌릴 데이터 이관도 없다. local/CI
MockTransport test client는 `async with` 종료 시 닫고, local DB를 사용한 외부
회귀 테스트만 해당 fixture의 seed cleanup 규칙을 따른다.

## 10. 승인된 완료 조건

- [ ] `Settings.ollama_embed_batch_size`가 기본 8, alias
  `STOCKAPP_OLLAMA_EMBED_BATCH_SIZE`, `ge=1`을 갖는다.
- [ ] `embed_articles(...) -> list[list[float]]` public signature과 `embed` alias가
  유지된다.
- [ ] 전체 input은 한 번 build되고, 동일 AsyncClient에서 청크가 순차 처리된다.
- [ ] 청크마다 기존 timeout 30초 기본값과 최대 retry 2회가 적용된다.
- [ ] 결과 순서와 cross-chunk dimension이 검증된다.
- [ ] final chunk failure 시 partial vector와 이후 request가 없고, cluster 전체가
  기존 singleton `UNAVAILABLE` fallback으로 처리된다.
- [ ] grouping은 전체 cluster vector에 대해 한 번만 실행되고 DB에는 vector가
  기록되지 않는다.
- [ ] 진단 및 redaction 테스트가 허용 숫자·reason만 남김을 증명한다.
- [ ] DB migration/schema 변경, algorithm version 변경, BatchLlm/Gemini 변경이
  없다.
- [ ] rollback 절차가 batch size 60과 worker restart로 문서화되어 있다.
