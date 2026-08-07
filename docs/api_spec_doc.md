# 백엔드 API 명세서

## 1. 문서 목적

본 문서는 현재 Frontend 화면 구조를 기준으로, 시장 일간 통합 페이지 조회, 아카이브 탐색, 뉴스 클러스터 상세 조회, 배치 실행/모니터링 기능을 제공하는 백엔드 API를 정의한다.

본 문서는 Backend 구현 전 단계의 Frontend-first 명세이며, 화면 렌더링 시 불필요한 다중 호출이 발생하지 않도록 다음 원칙을 따른다.

- 최신 시장 화면: API 1회 호출로 렌더링 가능해야 한다.
- 날짜별 시장 화면: API 1회 호출로 렌더링 가능해야 한다.
- 아카이브 검색 화면: API 1회 호출로 결과 목록을 렌더링 가능해야 한다.
- 뉴스 클러스터 상세 화면: API 1회 호출로 렌더링 가능해야 한다.
- 배치 상태 화면: 목록 API 1회, 선택 상세 API 1회 이내로 구성한다.

---

## 2. 공통 규칙

## 2-1. Base URL

```text
/stock/api
```

## 2-2. Content-Type

```http
Content-Type: application/json
```

## 2-3. 공통 응답 구조

### 성공 응답

```json
{
  "success": true,
  "data": {},
  "meta": {
    "requestId": "req-20260318-0001",
    "timestamp": "2026-03-18T09:00:00"
  }
}
```

### 실패 응답

실패 시 response code는 원인에 따라 400, 401, 403, 404, 409, 500 등을 반환한다.

```json
{
  "success": false,
  "error": {
    "code": "PAGE_NOT_FOUND",
    "message": "요청한 페이지를 찾을 수 없습니다."
  },
  "meta": {
    "requestId": "req-20260318-0001",
    "timestamp": "2026-03-18T09:00:00"
  }
}
```

## 2-4. 인증 정책

- `GET /health`를 제외한 모든 API는 인증이 필요하다.
- 인증 정보는 헤더에 담아 전달한다.
- JWT 토큰은 별도 인증 서비스에서 발급받은 토큰을 사용한다.
- 배치/운영성 로그에는 JWT에서 추출한 `user_id`를 저장한다.
- 역할(role)에 따라 접근 가능한 API가 다르다.
  - `USER`, `ADMIN`: 페이지/아카이브/뉴스 클러스터 조회 API (`/pages/*`, `/news/*`)
  - `ADMIN`, `CLIENT`: 배치 실행/조회 API (`/batch/*`)

```http
Authorization: Bearer {TOKEN}
```

## 2-5. Frontend 정렬/호출 정책

- 일간 페이지 응답은 Frontend가 별도 조합하지 않도록 통합 스냅샷 형태로 제공한다.
- 일간 페이지 응답에는 화면 렌더링에 필요한 헤더, 섹션, 기사 링크, 메타 정보를 모두 포함한다.
- 목록 화면에서 추가 호출을 유발하는 핵심 필드(`marketScope`, `durationSeconds`, `partialMessage`)는 목록 응답에도 포함한다.
- 상태값은 Frontend와 동일하게 `READY`, `PARTIAL`, `FAILED`, `SUCCESS`, `RUNNING`, `PENDING` 기준으로 사용한다.

---

## 3. API 목록

| 구분   | Method | Path                            | 설명                          |
| ------ | ------ | -------------------------------- | ----------------------------- |
| Batch  | POST   | `/batch/market-daily`            | 통합 일간 배치 실행           |
| Batch  | POST   | `/batch/news-collection`         | 네이버 뉴스 증분 수집 실행    |
| Batch  | GET    | `/batch/jobs`                    | 배치 목록 조회                |
| Batch  | GET    | `/batch/jobs/{jobId}`            | 배치 상세 조회                |
| Batch  | POST   | `/batch/jobs/{jobId}/retry-ai`   | 실패/Fallback AI 요약 재처리  |
| Page   | GET    | `/pages/daily/latest`            | 최신 통합 일간 페이지 조회    |
| Page   | GET    | `/pages/daily`                   | 날짜별 통합 일간 페이지 조회  |
| Page   | GET    | `/pages/archive`                 | 아카이브 목록 조회            |
| Page   | GET    | `/pages/{pageId}`                | 통합 페이지 상세 조회         |
| News   | GET    | `/news/clusters/{clusterId}`     | 뉴스 클러스터 상세 조회       |
| System | GET    | `/health`                        | 서비스 상태 점검              |

---

## 4. 공통 데이터 모델

## 4-1. 통합 일간 페이지 응답 모델

`/pages/daily/latest`, `/pages/daily`, `/pages/{pageId}`는 동일한 응답 구조를 사용한다.

```json
{
  "pageId": 501,
  "businessDate": "2026-03-17",
  "versionNo": 3,
  "pageTitle": "글로벌 시장 일간 요약 - 2026-03-17",
  "status": "READY",
  "globalHeadline": "기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세",
  "generatedAt": "2026-03-18T06:12:10",
  "partialMessage": null,
  "markets": [
    {
      "marketType": "US",
      "marketLabel": "미국 증시 일간 요약",
      "summaryTitle": "반도체와 대형 성장주가 시장 주도권을 회복",
      "summaryBody": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
      "analysis": {
        "background": ["대형 기술주 매수세 유입", "금리 우려는 잔존"],
        "keyThemes": ["AI", "반도체", "금리"],
        "outlook": "다음 거래일에는 CPI 발표와 대형주 실적이 중요 변수다."
      },
      "indices": [
        {
          "indexCode": "^IXIC",
          "indexName": "NASDAQ",
          "closePrice": 18250.12,
          "changeValue": 120.33,
          "changePercent": 0.66,
          "highPrice": 18300.1,
          "lowPrice": 18100.2,
          "sourceDate": "2026-03-17",
          "expectedSessionDate": "2026-03-17",
          "sessionCloseAt": "2026-03-17T20:00:00Z"
        }
      ],
      "topClusters": [
        {
          "clusterId": "51f0d9a0-9fc5-4f15-a4f9-62856f128683",
          "title": "엔비디아 및 반도체 강세에 기술주 상승",
          "summary": "반도체 업종 강세가 나스닥 상승을 견인했다.",
          "articleCount": 6,
          "tags": ["반도체", "AI", "나스닥"],
          "representativeArticle": {
            "title": "엔비디아 급등에 반도체 강세",
            "publisherName": "매일경제",
            "publishedAt": "2026-03-17T23:15:00",
            "originLink": "https://example.com/article1",
            "naverLink": "https://search.naver.com/article1"
          }
        }
      ],
      "articleLinks": [
        {
          "processedArticleId": 2001,
          "clusterId": "51f0d9a0-9fc5-4f15-a4f9-62856f128683",
          "clusterTitle": "엔비디아 및 반도체 강세에 기술주 상승",
          "title": "엔비디아 급등에 반도체 강세",
          "publisherName": "매일경제",
          "publishedAt": "2026-03-17T23:15:00",
          "originLink": "https://example.com/article1",
          "naverLink": "https://search.naver.com/article1"
        }
      ],
      "metadata": {
        "rawNewsCount": 27,
        "processedNewsCount": 18,
        "clusterCount": 5,
        "lastUpdatedAt": "2026-03-18T06:20:00Z",
        "partialMessage": null,
        "sourceDate": "2026-03-17",
        "expectedSessionDate": "2026-03-17",
        "sessionCloseAt": "2026-03-17T20:00:00Z",
        "newsWindowStartAt": "2026-03-16T22:00:00Z",
        "newsWindowEndAt": "2026-03-17T22:00:00Z",
        "coverageComplete": true
      }
    }
  ],
  "metadata": {
    "rawNewsCount": 174,
    "processedNewsCount": 114,
    "clusterCount": 21,
    "lastUpdatedAt": "2026-03-18T06:12:10",
    "isLatest": true
  }
}
```

### 모델 설계 의도

- `globalHeadline`: 화면 최상단 글로벌 한줄 요약
- `markets[]`: 현재 Frontend가 렌더링하는 미국/한국 섹션 단위
- `indices[]`: 지수 카드 렌더링용
- `topClusters[]`: 핵심 뉴스 카드 렌더링용
- `topClusters[].representativeArticle`: 카드 내 `원문 보기` 버튼용
- `articleLinks[]`: 페이지 하단 원문 기사 링크 리스트용
- `markets[].metadata`: 시장별 보조 메타 정보
- 최상위 `metadata`: 통합 페이지 메타 정보

---

## 5. 상세 API 명세

## 5-1. 통합 일간 배치 실행

### `POST /batch/market-daily`

### 설명

미국/한국 시장 데이터를 수집하고 통합 일간 페이지를 생성하는 배치를 실행한다. `ADMIN`, `CLIENT` 역할만 호출할 수 있다.
`rebuildPageOnly=true`로 호출하면 별도의 페이지 재생성 API 없이 이
API 하나로 뉴스/지수 재수집 없이 저장된 정제 결과만 재사용해 페이지
스냅샷을 재생성한다(항상 새 `versionNo`를 생성하고 기존 버전은 보존).

### Request Body

```json
{
  "businessDate": "2026-03-17",
  "force": false,
  "rebuildPageOnly": false
}
```

### Field 정의

| 필드            | 타입         | 필수 | 설명                                         |
| --------------- | ------------ | ---- | -------------------------------------------- |
| businessDate    | string(date) | N    | 미입력 시 한국 시간(UTC+9) 기준 기본 날짜 계산 |
| force           | boolean      | N    | 기존 결과가 있어도 재생성 허용 여부          |
| rebuildPageOnly | boolean      | N    | 뉴스/지수 재수집 없이 페이지 스냅샷만 재생성 |

### 처리 규칙

- 요청은 `batch_job.status=PENDING`으로 영속화한 뒤 HTTP 202를 반환한다
- HTTP 202 응답 후 FastAPI `BackgroundTasks`가 같은 API 프로세스의 durable
  queue drain을 시작한다
- 동일 `businessDate` 배치가 `PENDING` 또는 `RUNNING`이면 409 반환
- 선택적 `Idempotency-Key` 헤더를 재사용하면 같은 요청의 기존 job을 반환한다
- `businessDate`는 한국 시간(UTC+9) 기준 날짜를 사용한다
- `force=false`이고 생성 완료된 페이지가 존재하면 409를 반환한다
- `force=true`이면 기존 페이지를 덮어쓰지 않고 새 `versionNo`를 생성한다

### Response 202

```json
{
  "success": true,
  "data": {
    "jobId": 1001,
    "jobName": "market_daily_batch",
    "businessDate": "2026-03-17",
    "status": "PENDING",
    "startedAt": "2026-03-18T06:10:00",
    "queuedAt": "2026-03-18T06:10:00"
  },
  "meta": {
    "requestId": "req-001",
    "timestamp": "2026-03-18T06:10:00"
  }
}
```

### Error Code

| 코드                    | 설명                                       |
| ----------------------- | ------------------------------------------ |
| BATCH_ALREADY_RUNNING   | 동일 날짜 배치 실행 중 (409)                |
| PAGE_ALREADY_EXISTS     | force=false인데 페이지 존재 (409)           |
| PAGE_NOT_FOUND          | rebuildPageOnly=true인데 재생성할 페이지 없음 (404) |
| IDEMPOTENCY_KEY_REUSED  | 동일 키를 다른 요청에 재사용 (409)          |
| INTERNAL_BATCH_ERROR    | 내부 처리 오류                              |

---

## 5-2. 뉴스 수집 실행

### `POST /batch/news-collection`

### 설명

네이버 뉴스를 KST 30분 슬롯 단위로 증분 수집하는 배치를 실행한다.
`ADMIN`, `CLIENT` 역할만 호출할 수 있다.

### Request Body

```json
{
  "slotEndAt": "2026-03-18T06:30:00+09:00"
}
```

### Field 정의

| 필드      | 타입           | 필수 | 설명                                                          |
| --------- | -------------- | ---- | --------------------------------------------------------------- |
| slotEndAt | string(date-time) | N    | 미입력 시 가장 최근에 완료된 KST 30분 슬롯을 사용한다. 타임존 정보가 필수이며 KST 기준 0분/30분 경계여야 한다. |

### 처리 규칙

- 요청은 `batch_job.status=PENDING`으로 영속화한 뒤 HTTP 202를 반환한다.
- HTTP 202 응답 후 FastAPI `BackgroundTasks`가 같은 API 프로세스의 durable
  queue drain을 시작한다.
- 동일 `windowStartAt`/`windowEndAt` 슬롯의 수집 요청이 이미 존재하면
  기존 job을 그대로 반환한다(멱등).
- `slotEndAt`은 아직 완료되지 않은 슬롯이거나, 백필 허용 범위를 벗어나면
  거부된다.

### Response 202

```json
{
  "success": true,
  "data": {
    "jobId": 3001,
    "runId": 501,
    "jobName": "naver_news_collection",
    "status": "PENDING",
    "providerName": "NAVER_NEWS",
    "windowStartAt": "2026-03-18T06:00:00+09:00",
    "windowEndAt": "2026-03-18T06:30:00+09:00",
    "queryStartAt": "2026-03-18T05:55:00+09:00",
    "queryEndAt": "2026-03-18T06:30:00+09:00",
    "queuedAt": "2026-03-18T06:30:05+09:00"
  },
  "meta": {
    "requestId": "req-009",
    "timestamp": "2026-03-18T06:30:05"
  }
}
```

### Error Code

| 코드                    | 설명                                          |
| ----------------------- | --------------------------------------------- |
| NEWS_SLOT_INVALID       | `slotEndAt`에 타임존이 없거나 30분 경계가 아님 (409) |
| NEWS_SLOT_NOT_COMPLETED | 아직 완료되지 않은 슬롯을 요청 (409)          |
| NEWS_SLOT_OUT_OF_RANGE  | 백필 허용 범위를 벗어난 슬롯을 요청 (409)     |

---

## 5-3. 배치 목록 조회

### `GET /batch/jobs`

### Query Params

| 파라미터 | 타입         | 필수 | 설명                                                 |
| -------- | ------------ | ---- | ---------------------------------------------------- |
| fromDate | string(date) | N    | 시작일                                               |
| toDate   | string(date) | N    | 종료일                                               |
| status   | string       | N    | `PENDING`, `RUNNING`, `SUCCESS`, `PARTIAL`, `FAILED` |
| jobType  | string       | N    | `MARKET_SNAPSHOT`, `NEWS_COLLECTION`                 |
| page     | int          | N    | 기본 1                                               |
| size     | int          | N    | 기본 20, 최대 100                                    |

### Response 200

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "jobId": 1001,
        "jobType": "MARKET_SNAPSHOT",
        "jobName": "market_daily_batch",
        "businessDate": "2026-03-17",
        "status": "SUCCESS",
        "runMode": "FULL",
        "sourceJobId": null,
        "sourcePageId": null,
        "queuedAt": "2026-03-18T06:09:58",
        "attemptCount": 1,
        "maxAttempts": 3,
        "currentStep": "FINALIZE_JOB",
        "startedAt": "2026-03-18T06:10:00",
        "endedAt": "2026-03-18T06:12:15",
        "durationSeconds": 135,
        "marketScope": "GLOBAL",
        "rawNewsCount": 174,
        "processedNewsCount": 114,
        "clusterCount": 21,
        "pageId": 501,
        "pageVersionNo": 3,
        "partialMessage": null,
        "aiTargetCount": 45,
        "aiAttemptedCount": 3,
        "aiSuccessCount": 45,
        "aiFallbackCount": 0,
        "aiFailedCount": 0,
        "aiRecoveredCount": 3
      }
    ],
    "pagination": {
      "page": 1,
      "size": 20,
      "totalCount": 1
    },
    "summary": {
      "successCount": 17,
      "partialCount": 1,
      "failedCount": 0,
      "avgDurationSeconds": 862
    }
  },
  "meta": {
    "requestId": "req-002",
    "timestamp": "2026-03-18T06:20:00"
  }
}
```

### 비고

- 배치 목록 화면은 이 API만으로 표와 상단 통계 카드를 렌더링할 수 있어야 한다.
- `marketScope`는 현재 구조상 `GLOBAL` 고정이어도 유지한다.
- `jobType`은 `runMode`로부터 파생된다: `runMode=NEWS_COLLECTION`이면
  `NEWS_COLLECTION`, 그 외에는 `MARKET_SNAPSHOT`이다.

---

## 5-4. 배치 상세 조회

### `GET /batch/jobs/{jobId}`

### 설명

배치 목록의 항목별 상세 정보를 조회한다. `jobType=MARKET_SNAPSHOT`인
잡은 `snapshot`을, `jobType=NEWS_COLLECTION`인 잡은 `newsCollection`을
채워 반환하며 나머지 하나는 `null`이다.

### Response 200 (MARKET_SNAPSHOT)

```json
{
  "success": true,
  "data": {
    "jobId": 1001,
    "jobName": "market_daily_batch",
    "jobType": "MARKET_SNAPSHOT",
    "businessDate": "2026-03-17",
    "status": "SUCCESS",
    "runMode": "FULL",
    "sourceJobId": null,
    "sourcePageId": null,
    "queuedAt": "2026-03-18T06:09:58",
    "attemptCount": 1,
    "maxAttempts": 3,
    "currentStep": "FINALIZE_JOB",
    "startedAt": "2026-03-18T06:10:00",
    "endedAt": "2026-03-18T06:12:15",
    "durationSeconds": 135,
    "partialMessage": null,
    "errorCode": null,
    "errorMessage": null,
    "logSummary": "정상 처리. 시장 데이터, 기사 수집, 클러스터링이 SLA 안에서 종료됐다.",
    "snapshot": {
      "forceRun": false,
      "rebuildPageOnly": false,
      "rawNewsCount": 174,
      "processedNewsCount": 114,
      "clusterCount": 21,
      "pageId": 501,
      "pageVersionNo": 3,
      "aiTargetCount": 45,
      "aiAttemptedCount": 3,
      "aiSuccessCount": 45,
      "aiFallbackCount": 0,
      "aiFailedCount": 0,
      "aiRecoveredCount": 3
    },
    "newsCollection": null,
    "steps": [
      {
        "stepCode": "CREATE_JOB",
        "status": "SUCCEEDED",
        "startedAt": "2026-03-18T06:10:00+09:00",
        "endedAt": "2026-03-18T06:10:00+09:00",
        "durationMs": 12
      },
      {
        "stepCode": "DEDUPE_ARTICLES",
        "status": "SUCCEEDED",
        "startedAt": "2026-03-18T06:10:00+09:00",
        "endedAt": "2026-03-18T06:10:04+09:00",
        "durationMs": 4210
      }
    ]
  },
  "meta": {
    "requestId": "req-003",
    "timestamp": "2026-03-18T06:20:00"
  }
}
```

### Response 200 (NEWS_COLLECTION)

```json
{
  "success": true,
  "data": {
    "jobId": 3001,
    "jobName": "naver_news_collection",
    "jobType": "NEWS_COLLECTION",
    "businessDate": "2026-03-18",
    "status": "SUCCESS",
    "runMode": "NEWS_COLLECTION",
    "sourceJobId": null,
    "sourcePageId": null,
    "queuedAt": "2026-03-18T06:30:05",
    "attemptCount": 1,
    "maxAttempts": 3,
    "currentStep": "FINALIZE_JOB",
    "startedAt": "2026-03-18T06:30:06",
    "endedAt": "2026-03-18T06:30:42",
    "durationSeconds": 36,
    "partialMessage": null,
    "errorCode": null,
    "errorMessage": null,
    "logSummary": "정상 처리. 30분 슬롯 뉴스 수집이 정상 종료됐다.",
    "snapshot": null,
    "newsCollection": {
      "runId": 501,
      "providerName": "NAVER_NEWS",
      "windowStartAt": "2026-03-18T06:00:00+09:00",
      "windowEndAt": "2026-03-18T06:30:00+09:00",
      "queryStartAt": "2026-03-18T05:55:00+09:00",
      "queryEndAt": "2026-03-18T06:30:00+09:00",
      "totalKeywordCount": 40,
      "completedKeywordCount": 40,
      "fetchedCount": 512,
      "matchedCount": 96,
      "insertedCount": 82,
      "coverageComplete": true
    },
    "steps": [
      {
        "stepCode": "CREATE_JOB",
        "status": "SUCCEEDED",
        "startedAt": "2026-03-18T06:30:06+09:00",
        "endedAt": "2026-03-18T06:30:06+09:00",
        "durationMs": 8
      }
    ]
  },
  "meta": {
    "requestId": "req-010",
    "timestamp": "2026-03-18T06:31:00"
  }
}
```

`steps`는 해당 잡이 실행한 스텝을 실행 순서대로 담는다. `status`는
`RUNNING` / `SUCCEEDED` / `FAILED` 중 하나이며, 진행 중인 스텝은
`endedAt`과 `durationMs`가 `null`이다. 체크포인트 재개나 재시도로 같은
스텝이 여러 번 실행되면 항목도 여러 개 나타난다. AI 재처리 잡은 재처리
대상마다 생성 스텝 항목이 하나씩 생긴다. 이 기능 도입 이전에 실행된
잡은 빈 배열을 반환한다.

### Error Code

| 코드                | 설명                     |
| ------------------- | ------------------------ |
| BATCH_JOB_NOT_FOUND | 요청한 `jobId`가 없음 (404) |

---

## 5-5. AI 요약 재처리

### `POST /batch/jobs/{jobId}/retry-ai`

`ADMIN`, `CLIENT` 역할이 호출할 수 있다. 선택적 `Idempotency-Key` 헤더를
권장하며 동일 키의 동일 요청은 기존 `PENDING`/실행/완료 job을 반환한다.
동일 키를 다른 source job에 재사용하면 `409 IDEMPOTENCY_KEY_REUSED`를
반환한다.

### Response 202

```json
{
  "success": true,
  "data": {
    "jobId": 2001,
    "jobName": "market_daily_batch",
    "businessDate": "2026-03-17",
    "status": "PENDING",
    "runMode": "AI_RETRY",
    "sourceJobId": 1001,
    "sourcePageId": 501,
    "idempotencyKey": "ai-retry-1001-request-1",
    "startedAt": "2026-03-18T06:20:00+00:00"
  },
  "meta": {
    "requestId": "req-011",
    "timestamp": "2026-03-18T06:20:00"
  }
}
```

요청은 provider를 동기 호출하지 않는다. durable worker가 job을 claim한 뒤
원본 target 중 `FAILED`, `FALLBACK`, 또는 `fallbackUsed=true`인 항목만
재처리한다.

### Error Code

| 코드                          | 설명                                                 |
| ----------------------------- | ----------------------------------------------------- |
| BATCH_JOB_NOT_FOUND           | 요청한 source `jobId`가 없음 (404)                     |
| BATCH_JOB_NOT_TERMINAL        | source job이 아직 `PENDING`/`RUNNING`이라 재처리 불가 (409) |
| AI_RETRY_SOURCE_PAGE_NOT_FOUND | 재처리에 사용할 기존 페이지가 없음 (404)              |
| IDEMPOTENCY_KEY_REUSED        | 동일 키를 다른 source job에 재사용 (409)              |
| AI_RETRY_ALREADY_RUNNING      | 동일 대상의 AI 재처리 작업이 이미 실행 중 (409)       |

---

## 5-6. 최신 통합 일간 페이지 조회

### `GET /pages/daily/latest`

### 설명

가장 최근 생성된 통합 일간 페이지를 조회한다.

### Query Params

없음

### Response 200

응답 구조는 [4-1. 통합 일간 페이지 응답 모델]과 동일

### 404 예시

```json
{
  "success": false,
  "error": {
    "code": "LATEST_PAGE_NOT_FOUND",
    "message": "가장 최근 생성된 페이지가 존재하지 않습니다."
  },
  "meta": {
    "requestId": "req-004",
    "timestamp": "2026-03-18T06:21:00"
  }
}
```

---

## 5-7. 날짜별 통합 일간 페이지 조회

### `GET /pages/daily`

### Query Params

| 파라미터     | 타입         | 필수 | 설명                           |
| ------------ | ------------ | ---- | ------------------------------ |
| businessDate | string(date) | Y    | 조회 기준 날짜                 |
| versionNo    | int          | N    | 특정 버전 조회, 기본 최신 버전 |

### Response 200

응답 구조는 [4-1. 통합 일간 페이지 응답 모델]과 동일

### 404 예시

```json
{
  "success": false,
  "error": {
    "code": "PAGE_NOT_FOUND",
    "message": "요청한 날짜의 페이지가 존재하지 않습니다."
  },
  "meta": {
    "requestId": "req-005",
    "timestamp": "2026-03-18T06:21:40"
  }
}
```

`versionNo`를 지정했으나 해당 날짜에 그 버전이 없으면(해당 날짜 자체는
존재) `PAGE_VERSION_NOT_FOUND`를 반환한다.

---

## 5-8. 아카이브 목록 조회

### `GET /pages/archive`

### 설명

과거 통합 일간 페이지 목록을 조회한다. 아카이브 결과 행 클릭 시 날짜별 시장 페이지로 이동한다.

### Query Params

| 파라미터 | 타입         | 필수 | 설명                         |
| -------- | ------------ | ---- | ---------------------------- |
| fromDate | string(date) | N    | 시작일                       |
| toDate   | string(date) | N    | 종료일                       |
| status   | string       | N    | `READY`, `PARTIAL`, `FAILED` |
| page     | int          | N    | 기본 1                       |
| size     | int          | N    | 기본 30, 최대 100            |

### Response 200

```json
{
  "success": true,
  "data": {
    "items": [
      {
        "pageId": 501,
        "businessDate": "2026-03-17",
        "pageTitle": "글로벌 시장 일간 요약 - 2026-03-17",
        "headlineSummary": "기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세",
        "status": "READY",
        "generatedAt": "2026-03-18T06:12:10",
        "partialMessage": null
      }
    ],
    "pagination": {
      "page": 1,
      "size": 30,
      "totalCount": 1
    }
  },
  "meta": {
    "requestId": "req-006",
    "timestamp": "2026-03-18T06:22:00"
  }
}
```

### 비고

- 아카이브 결과는 클러스터 상세가 아니라 날짜별 시장 페이지 진입을 위한 목록이다.
- 결과 행을 그리기 위해 별도 상세 조회가 필요하지 않아야 한다.

### Error Code

| 코드                     | 설명                                   |
| ------------------------ | --------------------------------------- |
| UNSUPPORTED_ARCHIVE_STATUS | `status`가 `READY`/`PARTIAL`/`FAILED`가 아님 (400) |

---

## 5-9. 통합 페이지 상세 조회

### `GET /pages/{pageId}`

### 설명

페이지 ID를 알고 있을 때 통합 일간 페이지 상세 정보를 조회한다.

### Response 200

응답 구조는 [4-1. 통합 일간 페이지 응답 모델]과 동일

### 404 예시

```json
{
  "success": false,
  "error": {
    "code": "PAGE_NOT_FOUND",
    "message": "요청한 페이지를 찾을 수 없습니다."
  },
  "meta": {
    "requestId": "req-012",
    "timestamp": "2026-03-18T06:24:00"
  }
}
```

---

## 5-10. 뉴스 클러스터 상세 조회

### `GET /news/clusters/{clusterId}`

### 설명

뉴스 클러스터 상세 페이지를 조회한다. 화면 1회 호출로 제목, 태그, 심층 요약, 대표 기사, 관련 기사 목록, 메타 정보를 모두 내려준다.

### Response 200

```json
{
  "success": true,
  "data": {
    "clusterId": "51f0d9a0-9fc5-4f15-a4f9-62856f128683",
    "businessDate": "2026-03-17",
    "marketType": "US",
    "marketLabel": "미국",
    "title": "엔비디아 및 반도체 강세에 기술주 상승",
    "tags": ["반도체", "AI", "나스닥"],
    "summary": {
      "short": "반도체 업종 강세가 나스닥 상승을 견인했다.",
      "long": "엔비디아를 포함한 반도체 관련 종목이 강세를 보이며 기술주 중심 매수세가 확대되었다.",
      "analysis": [
        "연방준비제도의 금리 인하 경로가 더 명확해졌다는 해석이 확산되며 고밸류 성장주에 대한 할인율 부담이 완화됐다.",
        "엔비디아와 AMD를 포함한 반도체 업종은 AI 서버 수요와 차세대 칩 공개 기대가 동시에 반영되며 지수 대비 초과수익을 기록했다."
      ]
    },
    "articleCount": 6,
    "lastUpdatedAt": "2026-03-18T06:12:10",
    "representativeArticle": {
      "processedArticleId": 2001,
      "title": "엔비디아 급등에 반도체 강세",
      "publisherName": "매일경제",
      "publishedAt": "2026-03-17T23:15:00",
      "originLink": "https://example.com/article1",
      "naverLink": "https://search.naver.com/article1",
      "sourceSummary": "경제·금융 전문 매체"
    },
    "articles": [
      {
        "processedArticleId": 2001,
        "title": "엔비디아 급등에 반도체 강세",
        "publisherName": "매일경제",
        "publishedAt": "2026-03-17T23:15:00",
        "originLink": "https://example.com/article1",
        "naverLink": "https://search.naver.com/article1",
        "sourceSummary": "경제·금융 전문 매체"
      }
    ]
  },
  "meta": {
    "requestId": "req-007",
    "timestamp": "2026-03-18T06:23:20"
  }
}
```

### 비고

- 대표 기사와 관련 기사 목록을 분리 제공한다.
- 대표 기사도 `articles` 내 동일 항목을 포함할 수 있다.
- `summary.analysis`는 클러스터 심층 분석 문단 목록이며, `summary.short`/`summary.long`과 함께 `summary` 객체 하위에 포함된다.

### Error Code

| 코드                                  | 설명                             |
| -------------------------------------- | -------------------------------- |
| CLUSTER_NOT_FOUND                     | 요청한 `clusterId`가 없음 (404)    |
| CLUSTER_REPRESENTATIVE_ARTICLE_NOT_FOUND | 클러스터 대표 기사를 찾을 수 없음 (404) |

---

## 5-11. 서비스 상태 점검

### `GET /health`

### 설명

DB 연결 상태를 점검한다. 인증이 필요 없다. 응답은 공통 성공/실패 응답
포맷(`success`/`data`/`meta` 또는 `success`/`error`/`meta`)을 따르지
않는 별도의 단순 payload를 반환한다.

### Response 200

```json
{
  "status": "ok"
}
```

### Response 503

```json
{
  "success": false,
  "error": {
    "code": "HEALTH_DATABASE_UNAVAILABLE",
    "message": "Database is unavailable."
  },
  "meta": {
    "requestId": "req-008",
    "timestamp": "2026-03-18T06:24:30"
  }
}
```

---

## 6. 상태 코드 정책

| 상태 코드 | 사용 조건                                   |
| --------- | -------------------------------------------- |
| 200       | 정상 조회                                    |
| 202       | 배치 실행 요청 접수(비동기 처리)             |
| 400       | 잘못된 요청                                  |
| 401       | 인증 실패                                    |
| 403       | 권한 없음                                    |
| 404       | 리소스 없음                                  |
| 409       | 중복 실행/충돌                               |
| 422       | 요청 스키마 검증 실패(쿼리/바디/헤더 형식 오류) |
| 500       | 내부 서버 오류                               |
| 503       | 서비스 상태 점검(`GET /health`) 실패          |

---

## 7. Frontend 연동 요약

| 화면                      | 권장 API                                                  | 호출 수 |
| ------------------------- | --------------------------------------------------------- | ------- |
| 최신 시장 페이지          | `GET /pages/daily/latest`                                 | 1       |
| 날짜별 시장 페이지        | `GET /pages/daily?businessDate=...`                       | 1       |
| 아카이브 페이지           | `GET /pages/archive?...`                                  | 1       |
| 뉴스 클러스터 상세 페이지 | `GET /news/clusters/{clusterId}`                          | 1       |
| 배치 상태 페이지          | `GET /batch/jobs?...` + 필요 시 `GET /batch/jobs/{jobId}` | 1~2     |
| 배치 실행(수동)           | `POST /batch/market-daily`                                | 1       |
| 뉴스 수집 실행(수동)      | `POST /batch/news-collection`                             | 1       |
| AI 요약 재처리            | `POST /batch/jobs/{jobId}/retry-ai`                        | 1       |

---

## 8. 백엔드 구현 권장 구조

| 계층                | 역할                                       |
| ------------------- | ------------------------------------------ |
| Controller          | 요청/응답                                  |
| Application Service | 배치 오케스트레이션                        |
| Domain Service      | 통합 페이지 생성, 클러스터 요약, 메타 집계 |
| Repository          | DB 접근                                    |
| External Client     | 뉴스 수집 API, 지수 Provider, AI Provider  |

권장 모듈:

- `batch`
- `page`
- `news`
- `summary`
- `admin`
