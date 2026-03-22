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

- 모든 API는 인증이 필요하다.
- 인증 정보는 헤더에 담아 전달한다.
- JWT 토큰은 별도 인증 서비스에서 발급받은 토큰을 사용한다.
- 배치/운영성 로그에는 JWT에서 추출한 `user_id`를 저장한다.

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

| 구분  | Method | Path                         | 설명                         |
| ----- | ------ | ---------------------------- | ---------------------------- |
| Batch | GET    | `/batch/market-daily`        | 통합 일간 배치 실행          |
| Batch | GET    | `/batch/jobs`                | 배치 목록 조회               |
| Batch | GET    | `/batch/jobs/{jobId}`        | 배치 상세 조회               |
| Page  | GET    | `/pages/daily/latest`        | 최신 통합 일간 페이지 조회   |
| Page  | GET    | `/pages/daily`               | 날짜별 통합 일간 페이지 조회 |
| Page  | GET    | `/pages/archive`             | 아카이브 목록 조회           |
| Page  | GET    | `/pages/{pageId}`            | 통합 페이지 상세 조회        |
| News  | GET    | `/news/clusters/{clusterId}` | 뉴스 클러스터 상세 조회      |
| Admin | POST   | `/admin/pages/rebuild`       | 페이지 재생성                |
| Admin | GET    | `/admin/health`              | 서비스 상태 점검             |

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
          "lowPrice": 18100.2
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
        "rawNewsCount": 85,
        "processedNewsCount": 26,
        "clusterCount": 7,
        "lastUpdatedAt": "2026-03-18T06:12:10",
        "partialMessage": null
      }
    }
  ],
  "metadata": {
    "rawNewsCount": 174,
    "processedNewsCount": 114,
    "clusterCount": 21,
    "lastUpdatedAt": "2026-03-18T06:12:10"
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

### `GET /batch/market-daily`

### 설명

미국/한국 시장 데이터를 수집하고 통합 일간 페이지를 생성하는 배치를 실행한다.

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

- 동일 `businessDate` 배치가 `RUNNING`이면 409 반환
- `businessDate`는 한국 시간(UTC+9) 기준 날짜를 사용한다
- `force=false`이고 생성 완료된 페이지가 존재하면 409를 반환한다
- `force=true`이면 기존 페이지를 덮어쓰지 않고 새 `versionNo`를 생성한다

### Response 200

```json
{
  "success": true,
  "data": {
    "jobId": 1001,
    "jobName": "market_daily_batch",
    "businessDate": "2026-03-17",
    "status": "RUNNING",
    "startedAt": "2026-03-18T06:10:00"
  },
  "meta": {
    "requestId": "req-001",
    "timestamp": "2026-03-18T06:10:00"
  }
}
```

### Error Code

| 코드                  | 설명                        |
| --------------------- | --------------------------- |
| BATCH_ALREADY_RUNNING | 동일 날짜 배치 실행 중      |
| PAGE_ALREADY_EXISTS   | force=false인데 페이지 존재 |
| INTERNAL_BATCH_ERROR  | 내부 처리 오류              |

---

## 5-2. 배치 목록 조회

### `GET /batch/jobs`

### Query Params

| 파라미터 | 타입         | 필수 | 설명                                                 |
| -------- | ------------ | ---- | ---------------------------------------------------- |
| fromDate | string(date) | N    | 시작일                                               |
| toDate   | string(date) | N    | 종료일                                               |
| status   | string       | N    | `PENDING`, `RUNNING`, `SUCCESS`, `PARTIAL`, `FAILED` |
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
        "jobName": "market_daily_batch",
        "businessDate": "2026-03-17",
        "status": "SUCCESS",
        "startedAt": "2026-03-18T06:10:00",
        "endedAt": "2026-03-18T06:12:15",
        "durationSeconds": 135,
        "marketScope": "GLOBAL",
        "rawNewsCount": 174,
        "processedNewsCount": 114,
        "clusterCount": 21,
        "pageId": 501,
        "pageVersionNo": 3,
        "partialMessage": null
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

---

## 5-3. 배치 상세 조회

### `GET /batch/jobs/{jobId}`

### Response 200

```json
{
  "success": true,
  "data": {
    "jobId": 1001,
    "jobName": "market_daily_batch",
    "businessDate": "2026-03-17",
    "status": "SUCCESS",
    "forceRun": false,
    "rebuildPageOnly": false,
    "startedAt": "2026-03-18T06:10:00",
    "endedAt": "2026-03-18T06:12:15",
    "durationSeconds": 135,
    "rawNewsCount": 174,
    "processedNewsCount": 114,
    "clusterCount": 21,
    "pageId": 501,
    "pageVersionNo": 3,
    "partialMessage": null,
    "errorCode": null,
    "errorMessage": null,
    "logSummary": "정상 처리. 시장 데이터, 기사 수집, 클러스터링이 SLA 안에서 종료됐다."
  },
  "meta": {
    "requestId": "req-003",
    "timestamp": "2026-03-18T06:20:00"
  }
}
```

---

## 5-4. 최신 통합 일간 페이지 조회

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

## 5-5. 날짜별 통합 일간 페이지 조회

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

---

## 5-6. 아카이브 목록 조회

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
| size     | int          | N    | 기본 30                      |

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

---

## 5-7. 통합 페이지 상세 조회

### `GET /pages/{pageId}`

### 설명

페이지 ID를 알고 있을 때 통합 일간 페이지 상세 정보를 조회한다.

### Response 200

응답 구조는 [4-1. 통합 일간 페이지 응답 모델]과 동일

---

## 5-8. 뉴스 클러스터 상세 조회

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
      "summaryShort": "반도체 업종 강세가 나스닥 상승을 견인했다.",
      "summaryLong": "엔비디아를 포함한 반도체 관련 종목이 강세를 보이며 기술주 중심 매수세가 확대되었다."
    },
    "analysis": [
      "연방준비제도의 금리 인하 경로가 더 명확해졌다는 해석이 확산되며 고밸류 성장주에 대한 할인율 부담이 완화됐다.",
      "엔비디아와 AMD를 포함한 반도체 업종은 AI 서버 수요와 차세대 칩 공개 기대가 동시에 반영되며 지수 대비 초과수익을 기록했다."
    ],
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
        "naverLink": "https://search.naver.com/article1"
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

---

## 5-9. 페이지 재생성

### `POST /admin/pages/rebuild`

### Request Body

```json
{
  "businessDate": "2026-03-17",
  "reason": "AI prompt version updated",
  "rebuildPageOnly": true
}
```

### 처리 규칙

- 기존 최신 페이지를 기반으로 새 버전을 생성한다.
- `rebuildPageOnly=true`이면 저장된 정제 결과를 재사용하고 페이지 스냅샷만 재생성한다.
- 페이지 재생성은 항상 새 `versionNo`를 생성하며 기존 버전은 보존한다.

---

## 5-10. 서비스 상태 점검

### `GET /admin/health`

### Response 200

```json
{
  "success": true,
  "data": {
    "status": "UP",
    "database": "UP",
    "naverApi": "UP",
    "indexProvider": "UP",
    "aiService": "UP"
  },
  "meta": {
    "requestId": "req-008",
    "timestamp": "2026-03-18T06:24:30"
  }
}
```

---

## 6. 상태 코드 정책

| 상태 코드 | 사용 조건           |
| --------- | ------------------- |
| 200       | 정상 조회/정상 실행 |
| 201       | 신규 리소스 생성    |
| 400       | 잘못된 요청         |
| 401       | 인증 실패           |
| 403       | 권한 없음           |
| 404       | 리소스 없음         |
| 409       | 중복 실행/충돌      |
| 500       | 내부 서버 오류      |

---

## 7. Frontend 연동 요약

| 화면                      | 권장 API                                                  | 호출 수 |
| ------------------------- | --------------------------------------------------------- | ------- |
| 최신 시장 페이지          | `GET /pages/daily/latest`                                 | 1       |
| 날짜별 시장 페이지        | `GET /pages/daily?businessDate=...`                       | 1       |
| 아카이브 페이지           | `GET /pages/archive?...`                                  | 1       |
| 뉴스 클러스터 상세 페이지 | `GET /news/clusters/{clusterId}`                          | 1       |
| 배치 상태 페이지          | `GET /batch/jobs?...` + 필요 시 `GET /batch/jobs/{jobId}` | 1~2     |

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
