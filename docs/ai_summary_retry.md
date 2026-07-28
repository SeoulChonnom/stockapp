# AI summary retry workflow

`POST /stock/api/batch/jobs/{jobId}/retry-ai`는 원본 배치와 페이지를
수정하지 않고 `runMode=AI_RETRY`, `status=PENDING`인 durable job을
enqueue한다. 운영 호출에는 `Idempotency-Key`를 넣는 것을 권장한다.

## Target identity와 lineage

AI 결과는 다음 `target_key`로 식별한다.

- `GLOBAL_HEADLINE`
- `MARKET_SUMMARY:<market>`
- `CLUSTER_CARD_SUMMARY:<cluster_id>`
- `CLUSTER_DETAIL_ANALYSIS:<cluster_id>`

원본 summary는 `attempt_no=1`이다. retry summary는 직전 effective row를
`source_summary_id`로 가리키며 attempt를 증가시킨다. 한 retry job에는
target별 한 행만 허용한다. resume upsert는 기존 성공 행을 절대 덮어쓰지
않고 fallback/failed 행만 개선할 수 있다.

Effective 결과는 target별로 정상 `SUCCESS`를 먼저 선택하고, 성공이
없을 때만 가장 최신 attempt를 선택한다. 따라서 이후 retry가 다시
fallback되더라도 이전 성공 결과가 퇴행하지 않는다.

## Resume와 page 규칙

worker는 안정적인 step code를 checkpoint한다.

- `AI_RETRY_SELECT`
- `AI_RETRY_GENERATE`
- `AI_RETRY_BUILD_PAGE`
- `AI_RETRY_FINALIZE`

현재 retry job에 이미 성공한 target은 provider를 다시 호출하지 않는다.
페이지 생성까지 commit된 뒤 process가 종료되면 checkpoint의
`pageId/pageVersionNo`를 재사용하여 중복 버전을 만들지 않는다.

- 복구 0건: 새 페이지를 만들지 않는다.
- 일부 복구: source persisted page의 indices, clusters, article links를
  복제하고 개선된 AI field만 overlay한 `PARTIAL` vNext를 만든다.
- 전부 복구 + 비AI issue 없음: `READY` vNext를 만든다.
- 전부 복구 + 비AI issue 남음: `PARTIAL` vNext를 만든다.

페이지 `metadata_json.issues`는 `AI_SUMMARY`, `BATCH_PARTIAL`,
`BATCH_WARNING` category를 사용한다. 이 구조가 없는 과거 page는 기존
warnings와 partial message를 보수적으로 해석한다.

## Counts

batch list/detail API는 다음 typed count를 노출한다.

- `aiTargetCount`
- `aiAttemptedCount`
- `aiSuccessCount`
- `aiFallbackCount`
- `aiFailedCount`
- `aiRecoveredCount`

`aiAttemptedCount`는 provider 호출 횟수가 아니라 해당 retry job에
persist된 고유 target 수다. resume 때문에 같은 target을 다시 호출해도
count가 증가하지 않는다.

## Scope

이 workflow는 `GenerateAiSummariesStep`이 만드는 네 종류 summary만
복구한다. `BuildClustersStep.enrich_cluster`의 fallback 재처리는 입력
cluster 자체와 ranking을 바꿀 수 있으므로 P1 후속 과제이며 현재 범위에
포함되지 않는다.
