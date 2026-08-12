# FE 요청 B1~B5 백엔드 통합 설계

- 작성일: 2026-08-13
- 대상 저장소: `stockapp`, `stockfront`
- 기준 요청서: `../stockfront/docs/backend-requests-2026-08-12.md`
- 상태: 사용자 승인 완료

## 1. 목적과 범위

이 문서는 Market Brief UI 개선을 위해 프런트엔드가 요청한 B1~B5 계약을
백엔드에서 제공하는 방법을 정의한다.

포함 범위는 다음과 같다.

- B1: 페이지 전역 `오늘의 핵심`
- B2: 근거 기사에 연결된 구조화 클러스터 분석
- B3: 계층형 테마 분류와 아카이브 검색
- B4: 정확 중복 개수와 유사 기사 그룹
- B5: 실제 페이지가 존재하는 인접 영업일 탐색

FE와 BE는 함께 수정하고 테스트한 뒤 배포한다. 따라서 구형 필드 병행,
dual-write, deprecated 계약, 이관 후 구형 계약 폐기 절차는 설계하지 않는다.
DB 스키마 변경 자체는 적용하되 구형 API 호환용 컬럼이나 응답은 만들지 않는다.

## 2. 전체 데이터 흐름

```text
기사 수집·가공
    ├─ exact 중복 통합
    ↓
클러스터 생성
    ├─ Gemini enrichment
    │  ├─ 제목·요약·자유 태그
    │  └─ 표준 themeCodes
    ├─ Ollama bge-m3
    │  └─ 유사 기사 그룹
    ↓
AI 요약 생성
    ├─ 페이지 keyPoints
    └─ 근거 기반 클러스터 분석
    ↓
페이지 스냅샷
    ├─ 핵심 요약
    ├─ 계층형 테마
    └─ 기사 그룹 정보
    ↓
FastAPI
    ├─ 일간 페이지
    ├─ 클러스터 상세
    ├─ 아카이브 검색
    ├─ 테마 카탈로그
    └─ 인접 영업일
```

생성형 결과와 분류 결과는 API 요청 시 다시 계산하지 않는다. 배치가 결과를
검증하고 저장하며 읽기 API는 저장된 스냅샷을 조립한다.

## 3. B1: 오늘의 핵심

### 3.1 응답 계약

`DailyPageResponse`에 `keyPoints` 필수 필드를 추가한다.

```json
{
  "keyPoints": [
    {
      "kind": "direction",
      "label": "시장 방향",
      "direction": "MIXED",
      "text": "미국 증시는 상승했지만 한국 증시는 하락해 시장별 흐름이 엇갈렸습니다."
    },
    {
      "kind": "driver",
      "label": "주요 원인",
      "text": "금리 인하 기대와 국내 반도체주 약세가 주요 변동 요인이었습니다."
    },
    {
      "kind": "watch",
      "label": "관전 포인트",
      "text": "미국 물가 지표와 외국인의 반도체주 수급을 확인할 필요가 있습니다."
    }
  ]
}
```

방향 enum은 다음 네 값이다.

```text
UP | DOWN | MIXED | FLAT
```

계약 규칙은 다음과 같다.

- `keyPoints`는 항상 존재하며 `null`이나 필드 생략을 허용하지 않는다.
- 성공 시 `direction`, `driver`, `watch`를 정확히 하나씩 반환한다.
- 배열 순서는 `direction`, `driver`, `watch`로 고정한다.
- `direction` 필드는 `kind=direction` 항목에서만 필수이고 다른 kind에는 금지한다.
- label은 BE가 `시장 방향`, `주요 원인`, `관전 포인트`로 고정한다.
- text는 HTML, Markdown, 줄바꿈이 없는 완결된 한 문장이다.
- 일부 항목만 반환하지 않는다. 하나라도 검증에 실패하면 `keyPoints: []`이다.
- 다른 페이지 영역이 `PARTIAL`이어도 key point 생성이 성공했다면 3개를 제공한다.
- `globalHeadline`과 `keyPoints`의 성공 여부는 독립적이다.

생성 실패 시 페이지는 계속 제공하며 다음 이슈를 기록한다.
페이지 상태는 `PARTIAL`로 설정한다.

```json
{
  "category": "AI_SUMMARY",
  "code": "KEY_POINTS_GENERATION_FAILED",
  "message": "오늘의 핵심 요약을 생성하지 못했습니다."
}
```

### 3.2 생성과 저장

기존 전역 요약 입력인 US/KR 클러스터, 시장 지수와 등락률, 시장별 요약을
재사용한다. 검증을 통과한 전체 배열을
`market_daily_page.metadata_json.keyPoints`에 저장한다.

Pydantic 모델은 direction 전용 모델과 driver/watch 전용 모델을 식별 가능한
union으로 선언한다. 배열 길이, kind 중복, 순서를 서버 검증기로 확인한다.

## 4. B2: 근거 기반 클러스터 분석

### 4.1 최종 응답 계약

기존 `summary.analysis: string[]`은 제거하고 다음 구조로 교체한다.

```json
{
  "summary": {
    "short": "반도체주 약세가 국내 증시 하락을 주도했습니다.",
    "long": "외국인 매도와 업황 우려가 함께 반영됐습니다.",
    "analysisStatus": "READY",
    "analysisGeneratedAt": "2026-08-13T07:20:00+09:00",
    "analysisIssues": [],
    "conflictStatus": "NONE",
    "sections": [
      {
        "kind": "background",
        "title": "발생 배경",
        "paragraphs": [
          {
            "sentences": [
              {
                "text": "미국 반도체주 약세가 국내 시장으로 이어졌습니다.",
                "sourceArticleIds": [1024],
                "conflictStatus": "NONE",
                "conflictingSourceArticleIds": [],
                "conflictNote": null
              }
            ]
          }
        ]
      }
    ]
  }
}
```

enum은 다음과 같다.

```text
AnalysisStatus: READY | PARTIAL | UNAVAILABLE
AnalysisSectionKind: background | impact | related | outlook
ConflictStatus: NOT_CHECKED | NONE | FOUND
```

서버 고정 섹션 제목은 다음과 같다.

| kind | title |
|---|---|
| `background` | 발생 배경 |
| `impact` | 시장 영향 |
| `related` | 관련 업종·종목 |
| `outlook` | 향후 관전 포인트 |

### 4.2 구조와 근거 규칙

- `sections`는 필수 배열이며 `null`을 허용하지 않는다.
- 순서는 `background`, `impact`, `related`, `outlook`으로 고정한다.
- 동일 kind를 중복할 수 없다.
- 유효 문장이 없는 문단과 섹션은 제거한다.
- 분석 전체가 없으면 `sections: []`이다.
- 모든 문장은 최소 1개의 `sourceArticleIds`를 가져야 한다.
- `ClusterArticleResponse.processedArticleId`는 필수 정수로 변경한다.
- source ID는 같은 클러스터 응답의 `articles[].processedArticleId`만 참조한다.
- 존재하지 않거나 중복된 source ID는 검증 실패이다.
- 근거를 잃은 문장은 제거하고 빈 상위 컨테이너도 순차적으로 제거한다.

### 4.3 충돌 계약

충돌이 발견된 문장은 다음 정보를 제공한다.

```json
{
  "conflictStatus": "FOUND",
  "conflictingSourceArticleIds": [1042],
  "conflictNote": "기사별 외국인 순매매 방향이 다르게 보도됐습니다."
}
```

- `FOUND`이면 충돌 기사 ID가 1개 이상이고 conflict note가 필수다.
- `NONE` 또는 `NOT_CHECKED`이면 충돌 기사 배열은 비우고 note는 `null`이다.
- 지지 기사와 충돌 기사 ID는 서로 중복될 수 없다.
- 충돌한 기사도 같은 클러스터에 속해야 한다.
- conflict note는 차이를 설명하되 어느 기사가 옳은지 단정하지 않는다.
- 전체 conflict status 집계 우선순위는 `FOUND`, `NOT_CHECKED`, `NONE`이다.
- 충돌 발견은 정상 분석 결과이므로 그 자체로 `PARTIAL`이 되지 않는다.
- 충돌 검사를 완료하지 못한 경우 분석 상태는 `PARTIAL`이다.

### 4.4 분석 상태와 이슈

| 상태 | 조건 |
|---|---|
| `READY` | 유효 섹션이 있고 생성, 근거, 충돌 검증을 완료함 |
| `PARTIAL` | 유효 섹션은 있으나 일부 문장이 제거됐거나 충돌 검사가 미완료됨 |
| `UNAVAILABLE` | 표시할 유효 문장이 없음 |

`UNAVAILABLE` 응답은 다음과 같다.

```json
{
  "analysisStatus": "UNAVAILABLE",
  "analysisGeneratedAt": null,
  "analysisIssues": [
    {
      "code": "NO_GROUNDED_SENTENCES",
      "message": "근거를 확인할 수 있는 분석 문장이 없습니다."
    }
  ],
  "conflictStatus": "NOT_CHECKED",
  "sections": []
}
```

공개 이슈 코드는 다음 네 개로 제한한다.

```text
ANALYSIS_GENERATION_FAILED
NO_GROUNDED_SENTENCES
INVALID_SOURCE_REFERENCE
CONFLICT_CHECK_FAILED
```

분석 실패는 클러스터 상세 API 전체 HTTP 실패로 전파하지 않는다.

### 4.5 생성과 저장

- 기존 cluster detail AI 호출이 기사 ID, 제목, 요약, 본문 excerpt를 입력받는다.
- LLM은 구조화 섹션, 문장, 지지 기사 ID, 충돌 정보를 생성한다.
- 서버는 ID 집합, enum, 순서, 빈 컨테이너, 충돌 필드 조합을 검증한다.
- 결과는 최신 `CLUSTER_DETAIL_ANALYSIS` 타입 `ai_summary.paragraphs_json`에 저장한다.
- `analysisGeneratedAt`은 `ai_summary.generated_at`을 사용한다.
- `news_cluster.updated_at`은 분석 생성 시각으로 사용하지 않는다.

## 5. B3: 계층형 테마와 아카이브 검색

### 5.1 카탈로그 모델

테마 체계는 사람이 정의하는 기준 데이터이고 클러스터별 분류 결과만 배치에서
생성한다. 최상위 축은 다음 다섯 개다.

```text
MACRO
SECTOR
CORPORATE_EVENT
MARKET_FLOW
ALTERNATIVE_ASSET
```

초기 카탈로그는 최상위 5개, 중간 분류 18개, 최하위 테마 40개로 구성한다.
초기 최대 깊이는 3단계지만 API와 DB는 임의 깊이를 지원한다.

클러스터는 활성 최하위 테마를 최대 3개 가진다. rank 1이 대표 테마다.

```json
{
  "primaryThemeCode": "SECTOR_SEMICONDUCTORS_MEMORY_HBM",
  "themeCodes": [
    "SECTOR_SEMICONDUCTORS_MEMORY_HBM",
    "CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE",
    "MARKET_FLOW_INVESTOR_FOREIGN"
  ]
}
```

부모 테마는 클러스터에 저장하지 않고 검색 조건으로만 사용한다. `GENERAL`,
`OTHER`, `UNCLASSIFIED` 코드는 만들지 않는다.

### 5.2 초기 테마 트리

```text
MACRO
├─ MACRO_ECONOMIC_DATA
│  ├─ MACRO_ECONOMIC_DATA_INFLATION
│  └─ MACRO_ECONOMIC_DATA_EMPLOYMENT_GROWTH
├─ MACRO_MONETARY_MARKETS
│  ├─ MACRO_MONETARY_MARKETS_INTEREST_RATES_BONDS
│  ├─ MACRO_MONETARY_MARKETS_LIQUIDITY
│  └─ MACRO_MONETARY_MARKETS_FX
└─ MACRO_POLICY_RISK
   ├─ MACRO_POLICY_RISK_FISCAL_REGULATION
   └─ MACRO_POLICY_RISK_GEOPOLITICS_TRADE

SECTOR
├─ SECTOR_SEMICONDUCTORS
│  ├─ SECTOR_SEMICONDUCTORS_MEMORY_HBM
│  ├─ SECTOR_SEMICONDUCTORS_FOUNDRY_SYSTEM
│  └─ SECTOR_SEMICONDUCTORS_EQUIPMENT_MATERIALS
├─ SECTOR_AI_SOFTWARE
│  ├─ SECTOR_AI_SOFTWARE_AI_INFRASTRUCTURE
│  └─ SECTOR_AI_SOFTWARE_CLOUD_PLATFORM
├─ SECTOR_FINANCIALS
│  ├─ SECTOR_FINANCIALS_BANKING
│  └─ SECTOR_FINANCIALS_SECURITIES_INSURANCE
├─ SECTOR_AUTOS_MOBILITY
│  ├─ SECTOR_AUTOS_MOBILITY_AUTOMAKERS_COMPONENTS
│  └─ SECTOR_AUTOS_MOBILITY_EV_BATTERY
├─ SECTOR_BIO_HEALTHCARE
│  ├─ SECTOR_BIO_HEALTHCARE_PHARMA_BIOTECH
│  └─ SECTOR_BIO_HEALTHCARE_MEDICAL_SERVICES
├─ SECTOR_CONSUMER_CONTENT
│  ├─ SECTOR_CONSUMER_CONTENT_RETAIL_ECOMMERCE
│  └─ SECTOR_CONSUMER_CONTENT_BRANDS_MEDIA_GAMING
├─ SECTOR_INDUSTRIALS_INFRA
│  ├─ SECTOR_INDUSTRIALS_INFRA_SHIPBUILDING_DEFENSE
│  ├─ SECTOR_INDUSTRIALS_INFRA_CONSTRUCTION_POWER
│  └─ SECTOR_INDUSTRIALS_INFRA_TRANSPORT_LOGISTICS
└─ SECTOR_ENERGY_MATERIALS
   ├─ SECTOR_ENERGY_MATERIALS_OIL_GAS
   └─ SECTOR_ENERGY_MATERIALS_STEEL_CHEMICALS

CORPORATE_EVENT
├─ CORPORATE_EVENT_PERFORMANCE
│  ├─ CORPORATE_EVENT_PERFORMANCE_EARNINGS_GUIDANCE
│  └─ CORPORATE_EVENT_PERFORMANCE_ORDERS_CONTRACTS
├─ CORPORATE_EVENT_CAPITAL_ACTION
│  ├─ CORPORATE_EVENT_CAPITAL_ACTION_MNA
│  ├─ CORPORATE_EVENT_CAPITAL_ACTION_IPO_CAPITAL_RAISE
│  └─ CORPORATE_EVENT_CAPITAL_ACTION_DIVIDEND_BUYBACK
└─ CORPORATE_EVENT_GOVERNANCE
   └─ CORPORATE_EVENT_GOVERNANCE_MANAGEMENT

MARKET_FLOW
├─ MARKET_FLOW_INVESTOR
│  ├─ MARKET_FLOW_INVESTOR_FOREIGN
│  ├─ MARKET_FLOW_INVESTOR_INSTITUTIONAL
│  └─ MARKET_FLOW_INVESTOR_RETAIL
└─ MARKET_FLOW_POSITIONING
   ├─ MARKET_FLOW_POSITIONING_SHORT_SELLING
   ├─ MARKET_FLOW_POSITIONING_ETF_REBALANCING
   └─ MARKET_FLOW_POSITIONING_VOLATILITY_SENTIMENT

ALTERNATIVE_ASSET
├─ ALTERNATIVE_ASSET_COMMODITIES
│  ├─ ALTERNATIVE_ASSET_COMMODITIES_ENERGY_PRICES
│  └─ ALTERNATIVE_ASSET_COMMODITIES_METALS_AGRICULTURE
└─ ALTERNATIVE_ASSET_DIGITAL
   └─ ALTERNATIVE_ASSET_DIGITAL_CRYPTO
```

### 5.3 DB 구조

`theme_catalog`:

```text
code              PK
parent_code       self FK, nullable
label             NOT NULL
description       NOT NULL
sort_order        NOT NULL
is_active         NOT NULL
created_at        NOT NULL
updated_at        NOT NULL
```

- code는 대문자 영문, 숫자, underscore만 허용한다.
- 자기 자신을 부모로 지정할 수 없다.
- 같은 부모 아래 sort order는 중복될 수 없다.
- 순환 관계는 seed 검증에서 거부한다.
- 기존 code의 의미를 다른 테마로 재사용하지 않는다.

`news_cluster_theme`:

```text
cluster_id             FK → news_cluster, ON DELETE CASCADE
theme_code             FK → theme_catalog
rank                   1..3
classification_method  LLM | KEYWORD_FALLBACK
classified_at          NOT NULL

PK      (cluster_id, theme_code)
UNIQUE  (cluster_id, rank)
```

`market_daily_page_market_cluster_theme`:

```text
page_market_cluster_id FK → market_daily_page_market_cluster, ON DELETE CASCADE
theme_code             FK → theme_catalog
rank                   1..3

PK      (page_market_cluster_id, theme_code)
UNIQUE  (page_market_cluster_id, rank)
```

아카이브 검색은 `news_cluster_theme`가 아닌 페이지 스냅샷 관계를 사용한다.

인덱스:

```text
theme_catalog(parent_code, sort_order)
news_cluster_theme(theme_code, cluster_id)
market_daily_page_market_cluster_theme(theme_code, page_market_cluster_id)
```

부모 테마 검색은 재귀 CTE로 자기 자신과 모든 활성 하위 노드를 포함한다.
별도의 closure table은 만들지 않는다.

### 5.4 규칙 관리

DB는 카탈로그와 FK 무결성을 담당하고 분류 규칙은 Git에서 관리한다.

```text
app/batch/theme_rules.yaml
```

각 최하위 테마에는 다음 구조를 정의한다.

```yaml
inclusionCriteria: []
exclusionCriteria: []

fallback:
  enabled: true
  minimumScore: 6
  strongPhrases: []
  supportingTermGroups: []
  excludedPhrases: []
```

검증 규칙:

- 활성 최하위 테마 40개 모두 분류 정의를 가져야 한다.
- 부모 테마에는 fallback 규칙을 둘 수 없다.
- DB seed에 없는 코드를 사용할 수 없다.
- 빈 문구와 중복 문구를 허용하지 않는다.
- 활성 fallback에는 평가 fixture가 필수다.
- 규칙 파일 파싱 또는 DB seed와의 정합성 검증 실패 시 배치를 시작하지 않는다.

### 5.5 배치 분류 전략

먼저 기존 `BUILD_CLUSTERS` enrichment 요청에 `themeCodes`를 포함하는 방식만
구현한다.

```text
활성 최하위 카탈로그 로딩
    ↓
기존 enrichment 요청에 포함
    ↓
기존 제목·요약·태그 검증
    └─ themeCodes와 독립
    ↓
themeCodes 검증
    ↓
fallback
    ↓
클러스터·기사·테마 관계 저장
```

- 활성 최하위 코드만 허용한다.
- 입력 순서를 유지한 채 중복을 제거한다.
- 최대 3개만 사용한다.
- 부모 코드와 비활성 코드는 무효다.
- 일부 코드만 유효하면 유효한 코드만 사용한다.
- 유효 코드가 없으면 fallback을 수행한다.
- theme 파싱 실패가 기존 enrichment 결과를 무효화하지 않도록 파서를 분리한다.

초기 방식의 품질 게이트:

- KR 20개와 US 20개 클러스터를 각각 3회 실행한다.
- 변경 전과 변경 후를 비교해 총 240회 호출한다.
- enrichment 성공률 하락은 1%p 이하여야 한다.
- 잘못된 테마 코드 반환률은 2% 이하여야 한다.
- fallback 포함 테마 부여 성공률은 95% 이상이어야 한다.
- 대표 테마 수동 검토 정확도는 90% 이상이어야 한다.
- 대표 테마 3회 반복 일치율은 80% 이상이어야 한다.
- p95 응답 시간 증가는 20% 이하여야 한다.
- 평균 토큰 사용량 증가는 25% 이하여야 한다.

한 번 프롬프트와 검증을 보정한 뒤에도 실패하면 초기 방식 전용 코드를 제거하고
다음 독립 단계를 구현한다.

```text
BUILD_CLUSTERS
    ↓
CLASSIFY_CLUSTER_THEMES
    ↓
GENERATE_AI_SUMMARIES
```

두 방식을 동시에 구현하거나 feature flag로 런타임 전환하지 않는다. 카탈로그,
검증기, 저장소, 평가 도구만 공통으로 유지한다.

### 5.6 precision-first fallback

모든 40개 테마는 LLM용 포함·제외 정의를 갖지만 검증된 테마만 규칙 fallback을
활성화한다. 최초 평가 후보는 다음 20개다.

- 물가·인플레이션
- 금리·채권
- 환율·외환
- 메모리·HBM
- 파운드리·시스템반도체
- 반도체 장비·소재
- 전기차·배터리
- 인수·합병
- 배당·자사주
- 외국인 수급
- 공매도
- 가상자산
- 고용·경기성장
- 완성차·부품
- 제약·바이오
- 조선·방산
- 실적·전망
- 수주·계약
- IPO·자금조달
- 기관 수급

테마별 활성화 기준:

- 양성 사례 10개 이상
- 경계 사례 5개 이상
- 음성 사례 10개 이상
- 정밀도 95% 이상
- 양성 재현율 70% 이상
- 음성 오탐률 5% 이하
- 동일 입력의 반복 결과 100% 일치

fallback 입력은 클러스터 제목, 대표 기사 제목, 전체 기사 제목, 원문 요약,
본문 excerpt다. LLM 분석과 LLM 자유 태그는 fallback 근거로 사용하지 않는다.

점수 기준은 다음과 같다.

| 증거 | 점수 |
|---|---:|
| 클러스터 제목 일치 | 5 |
| 대표 기사 제목 일치 | 4 |
| 일반 기사 제목 일치 | 기사당 3, 최대 6 |
| 기사 요약 또는 excerpt 일치 | 기사당 1, 최대 3 |
| strong phrase 일치 | 추가 2 |
| excluded phrase 일치 | 후보 제거 |

총점 6점 이상이며 제목에서 strong phrase가 확인되거나 서로 다른 기사 2개
이상에서 증거가 확인된 경우에만 후보가 된다. 여러 후보가 통과하면 같은 증거로
활성화된 유사 형제 테마를 제거한 후 독립 증거가 있는 상위 3개를 선택한다.
동점은 증거 기사 수, 카탈로그 sort order, theme code 순으로 해결한다.

fallback까지 실패한 클러스터는 테마 없이 저장한다. 해당 페이지는 `PARTIAL`이며
다음 이슈를 제공한다.

```json
{
  "category": "THEME_CLASSIFICATION",
  "code": "THEME_CLASSIFICATION_MISSING",
  "message": "일부 뉴스 주제의 검색 테마를 분류하지 못했습니다."
}
```

카탈로그 설정 오류와 테마 DB 저장 오류는 배치 단계 자체를 실패시킨다.

### 5.7 테마 카탈로그 API

```http
GET /pages/archive/themes
```

```json
{
  "items": [
    {
      "code": "SECTOR",
      "label": "업종",
      "description": "기업의 주요 사업 영역",
      "sortOrder": 2,
      "children": [
        {
          "code": "SECTOR_SEMICONDUCTORS",
          "label": "반도체",
          "description": "반도체 산업",
          "sortOrder": 1,
          "children": []
        }
      ]
    }
  ]
}
```

- 활성 노드만 반환한다.
- `children`은 항상 배열이다.
- 비활성 부모 아래의 노드는 모두 제외한다.
- 같은 계층에서 `sortOrder ASC`, `code ASC`로 정렬한다.
- 모든 반환 노드는 검색 조건으로 선택할 수 있다.

### 5.8 아카이브 검색 API

```http
GET /pages/archive
  ?fromDate=2026-08-01
  &toDate=2026-08-31
  &status=READY
  &marketType=KR
  &theme=SECTOR_SEMICONDUCTORS
  &theme=CORPORATE_EVENT_PERFORMANCE
  &q=외국인%20매수
  &page=1
  &size=30
```

파라미터 계약:

- `marketType`은 `US` 또는 `KR`이다.
- `theme`은 반복 가능하며 최대 10개다.
- 복수 theme끼리는 OR, 다른 종류의 필터와는 AND다.
- 부모 theme은 자기 자신과 모든 활성 하위 theme을 포함한다.
- 알 수 없거나 비활성인 theme이 하나라도 있으면 `422 INVALID_THEME`다.
- 공개 status 필터는 `READY`와 `PARTIAL`만 허용한다.
- 결과 단위는 페이지이고 동일 페이지를 중복 반환하지 않는다.

`q` 규칙:

- Unicode NFC 정규화를 적용한다.
- 영문은 casefold한다.
- 앞뒤와 연속 공백을 정리한다.
- 정규화 후 길이는 2~100자다.
- 공백 기준 토큰은 최대 10개다.
- 모든 토큰은 AND다.
- 유사어, 오타, 동의어 확장은 하지 않는다.
- 정렬은 `businessDate DESC`를 유지한다.

조건별 검색 단위:

- q만 있으면 페이지, 하나의 시장 요약, 하나의 클러스터 중 한 단위가 모든 토큰을 만족해야 한다.
- `marketType + q`는 선택 시장의 요약 또는 선택 시장의 한 클러스터가 만족해야 한다.
- `theme + q`는 선택 테마를 가진 같은 클러스터가 모든 토큰을 만족해야 한다.
- `marketType + theme + q`는 한 클러스터가 세 조건을 모두 만족해야 한다.
- 토큰은 같은 검색 단위의 서로 다른 필드에 존재할 수 있다.

페이지 시장과 페이지 클러스터 스냅샷에는 정규화된 `search_document`를 저장한다.
`pg_trgm` GIN 인덱스를 사용하며 필터 적용 후 total count와 pagination을 계산한다.

## 6. B4: 정확 중복과 유사 기사 그룹

### 6.1 개념 구분

- exact duplicate는 raw 기사 여러 건이 하나의 processed 기사로 통합된 관계다.
- similar group은 같은 클러스터 안의 processed 기사들이 같은 사건을 유사하게 다룬 관계다.
- exact duplicate와 similar group을 하나의 ID로 표현하지 않는다.

### 6.2 API 계약

클러스터 상세 응답에 그룹 상태를 추가하고 기존 평면 `articles[]`를 유지한다.

```json
{
  "articleGrouping": {
    "status": "READY",
    "generatedAt": "2026-08-13T07:20:00+09:00",
    "issue": null
  },
  "articles": [
    {
      "processedArticleId": 1024,
      "similarGroupId": "sim-cluster-41-1",
      "isSimilarGroupRepresentative": true,
      "exactDuplicateCount": 2
    }
  ]
}
```

`ArticleGroupingStatus`는 `READY` 또는 `UNAVAILABLE`이다.

- 모든 기사는 정확히 하나의 similar group에 속한다.
- 유사 기사가 없는 기사도 단독 그룹을 가진다.
- 그룹당 대표 기사는 정확히 하나다.
- 공개 그룹 ID는 `sim-{clusterUid}-{groupRank}` 형식으로 생성한다.
- exact duplicate count는 canonical 기사 자신을 제외한 통합 raw 기사 수다.
- 다른 processed 기사는 exact duplicate count에 포함하지 않는다.
- FE 필터 후 서버 대표 기사가 사라지면 남은 서버 순서의 첫 기사를 화면 대표로 사용한다.
- 그룹 내 표시 기사가 한 건만 남으면 접기 UI를 표시하지 않는다.

### 6.3 Ollama BGE-M3 유사도

모델은 Ollama가 관리하고 BE는 `POST /api/embed`를 호출한다. Ollama 표준 응답은
dense 벡터만 제공하므로 lexical 점수는 BE가 결정적으로 계산한다.

```text
denseScore   = Ollama bge-m3 cosine similarity
lexicalScore = 토큰, 숫자, 종목명, 기관명 일치

combinedScore =
    denseScore × denseWeight
    + lexicalScore × lexicalWeight
```

- 별도 LLM이나 별도 임베딩 모델을 사용하지 않는다.
- 한 클러스터의 기사 전체를 문자열 배열로 batch 요청한다.
- Ollama 요청은 `truncate=false`로 호출한다.
- 모델명, base URL, timeout을 환경 설정으로 관리한다.
- 입력은 canonical title과 source summary 또는 body excerpt로 구성한다.
- 초기 입력 길이는 512토큰 상당으로 제한한다.
- 반환 벡터 개수, 차원, 유한값 여부를 검증한다.
- 임베딩은 DB에 저장하지 않고 해당 배치 실행 중에만 사용한다.
- pgvector는 도입하지 않는다.

### 6.4 그룹 생성

완전 연결 방식으로 그룹화한다.

- 먼저 클러스터 안의 모든 기사 쌍 점수를 한 번 계산한다.
- 새 기사가 그룹의 모든 기존 기사와 임계값을 통과해야 그룹에 들어간다.
- 수치, 날짜, 방향 상충이 있으면 자동 병합하지 않는다.
- 연쇄 병합을 허용하지 않는다.
- 어떤 그룹에도 들어가지 못한 기사는 단독 그룹이 된다.
- 기사 입력 정렬과 동점 규칙을 고정해 반복 결과를 결정적으로 만든다.

시장별 임계값은 만들지 않고 전체 시장 공통값을 사용한다. 최소 300개 기사 쌍을
70% calibration, 30% holdout으로 나눠 가중치와 임계값을 고정한다.

합격 기준:

- 유사 그룹 정밀도 95% 이상
- 동일 사건 재현율 85% 이상
- 다른 사건 오병합률 3% 이하
- 수치·방향 hard-negative 오병합 0건
- 동일 입력 반복 그룹 결과 100% 일치
- 클러스터별 p95 처리 시간이 배치 운영 한도 이내

### 6.5 대표 기사

```text
representativeScore =
    그룹 내 평균 유사도 × 0.70
    + 정보 완전성 × 0.20
    + 최신성 × 0.10
```

정보 완전성은 원문 요약, 본문 excerpt, 언론사, 원문 링크, 유효 게시 시각의
존재 여부로 계산한다. 동점은 `publishedAt DESC`, `processedArticleId ASC` 순으로
해결한다.

- 단독 그룹은 해당 기사가 대표다.
- 언론사별 신뢰도나 우선순위를 두지 않는다.
- exact duplicate count가 많다는 이유로 대표 우선권을 주지 않는다.
- 기존 클러스터 대표 기사도 자동 우선하지 않는다.

### 6.6 저장

```text
news_cluster_similar_group
- id
- cluster_id
- group_rank
- representative_article_id
- algorithm_version
- generated_at

news_cluster_similar_group_article
- similar_group_id
- processed_article_id
- similarity_score
- exact_duplicate_count
- is_representative
- article_rank
```

PK와 FK 외에 `(cluster_id, group_rank)`, `(similar_group_id, article_rank)`를
unique로 제한한다. 클러스터 재실행 시 같은 트랜잭션에서 그룹과 구성원을
교체한다.

`news_cluster`에는 그룹 생성 상태를 저장한다.

```text
article_grouping_status        READY | UNAVAILABLE
article_grouping_generated_at  nullable
article_grouping_issue_code    nullable
```

`UNAVAILABLE`의 공개 문구는 issue code로부터 서버가 조립하고 내부 예외 문자열을
저장하거나 노출하지 않는다. 페이지 스냅샷 클러스터에도 같은 상태와 생성 시각,
공개 issue code를 복사한다.

페이지 스냅샷의 article link에도 similar group ID, 대표 여부, exact duplicate
count를 복사한다. API 요청 시 그룹을 다시 계산하지 않는다.

### 6.7 실패 처리

Ollama 연결 실패와 timeout 같은 일시 오류만 최대 2회 재시도한다. 모델 미설치,
벡터 개수·차원 불일치, NaN 또는 무한대 벡터는 재시도 없이 실패한다.

실패는 클러스터 단위로 격리한다.

```json
{
  "articleGrouping": {
    "status": "UNAVAILABLE",
    "generatedAt": null,
    "issue": {
      "code": "SIMILARITY_GROUPING_FAILED",
      "message": "유사 기사 묶음을 생성하지 못했습니다."
    }
  }
}
```

- 실패한 클러스터의 모든 기사를 단독 그룹으로 반환한다.
- `exactDuplicateCount`는 임베딩과 무관하므로 계속 제공한다.
- 페이지 전체 상태를 `PARTIAL`로 변경하지 않는다.
- FE는 `UNAVAILABLE`일 때 접기 UI를 표시하지 않는다.
- 공개 메시지에 Ollama endpoint, 모델 경로, 내부 예외를 노출하지 않는다.

## 7. B5: 인접 영업일

### 7.1 Endpoint

```http
GET /pages/navigation?businessDate=2026-08-13
```

```json
{
  "businessDate": "2026-08-13",
  "pageExists": false,
  "previousBusinessDate": "2026-08-12",
  "nextBusinessDate": "2026-08-14"
}
```

- 유효한 날짜는 페이지 존재 여부와 관계없이 200을 반환한다.
- 이전과 다음 키는 항상 존재하며 이웃이 없으면 `null`이다.
- 조회 범위 제한을 두지 않는다.
- 요청 날짜 자체는 이전 또는 다음에 포함하지 않는다.
- 달력 계산이 아니라 DB에 표시 가능한 페이지가 존재하는 날짜를 사용한다.
- 날짜 형식 오류는 422다.
- DB 조회 실패만 500이다.
- 기존 page read 인증 정책을 그대로 적용한다.

### 7.2 표시 가능한 페이지 선택

일반 공개 읽기 API는 날짜별 `READY` 또는 `PARTIAL` 버전 중 가장 높은
`versionNo`, 그다음 가장 높은 ID를 선택한다.

적용 대상:

- `GET /pages/daily/latest`
- `GET /pages/daily?businessDate=...`
- `GET /pages/archive`
- `GET /pages/navigation`

최신 절대 버전이 `FAILED`여도 이전 표시 가능 버전을 사용한다. `FAILED` 버전은
일반 공개 탐색에서 제외하고 운영 또는 명시적 버전 조회에서만 확인한다.

`pageExists`는 요청 날짜에 표시 가능한 버전이 하나 이상 존재하는지를 뜻한다.
이전과 다음 날짜도 같은 기준으로 선택한다. 기존 `DailyPageResponse.navigation`은
같은 repository 로직을 사용한다.

아카이브 쿼리의 처리 순서는 다음과 같다.

```text
1. READY/PARTIAL 후보 선택
2. 날짜별 최신 표시 가능 버전 선택
3. status, 날짜, 시장, 테마, q 필터 적용
4. businessDate 내림차순 정렬
5. total count와 pagination 적용
```

따라서 최신 공개 버전이 `PARTIAL`이고 이전 버전이 `READY`이면 `status=READY`
결과에 포함하지 않는다.

## 8. 오류와 공개 진단 원칙

- 콘텐츠 일부 생성 실패는 가능한 경우 정상 HTTP 응답 안의 구조화 상태로 격리한다.
- DB 무결성 또는 필수 설정 오류는 배치 또는 요청을 실패시킨다.
- 공개 메시지는 사용자가 이해할 수 있는 고정 문구만 사용한다.
- provider endpoint, 모델 파일 경로, SQL, stack trace를 공개 응답에 포함하지 않는다.
- 상세 예외와 평가 점수는 배치 event와 서버 로그에만 기록한다.
- 알 수 없는 enum과 잘못된 query 형식은 FastAPI/Pydantic 422로 처리한다.
- 도메인 오류는 안정적인 error code를 함께 제공한다.

## 9. 테스트와 품질 게이트

### 9.1 자동화 테스트

- Pydantic 필수성, enum, 배열 길이와 순서
- B1 전체 성공 및 원자적 빈 배열 실패
- B2 source ID 부분집합, 빈 컨테이너 제거, conflict 집계
- OpenAPI 계약 snapshot
- API 정상, 빈 결과, 422, 500
- 날짜별 최신 공개 버전 선택
- 필터 전에 과거 버전이 선택되지 않는 repository 회귀 테스트
- 부모 테마 재귀 검색과 비활성 노드 제외
- 복수 테마 OR와 다른 필터 AND
- 시장, 테마, q의 동일 클러스터 상관관계
- fallback 양성, 경계, 음성 fixture
- complete-link 그룹과 연쇄 병합 방지
- 대표 기사 점수 및 동점 결정성
- Ollama timeout, 모델 부재, 벡터 개수·차원·유한값 검증
- 스냅샷 복사와 FK 무결성
- FE 타입과 API spec 동기화

### 9.2 모델 품질 평가

모델 품질 평가는 일반 단위 테스트와 분리된 명시적 명령으로 실행한다.

- 테마 enrichment 변경 전후 240회 평가
- fallback 테마별 양성, 경계, 음성 평가
- 유사 기사 300쌍 calibration/holdout 평가
- 모델명, 알고리즘 버전, 가중치, 임계값을 결과에 기록
- 합격 기준 미달 시 배포를 중단한다.

## 10. FE 계약과 변경 경계

제거 또는 교체되는 항목:

- `summary.analysis: string[]` 제거
- FE의 ±90일 인접 날짜 우회 조회 제거
- 자유 `tags`를 아카이브 테마 필터값으로 사용하지 않음
- 모호한 `duplicateGroupId` 대신 similar group 필드 사용
- 아카이브 필터가 최신 버전 선택보다 먼저 적용되는 기존 쿼리 수정

유지되는 항목:

- `globalHeadline`
- 시장별 기존 분석
- 화면 표시용 자유 `tags`
- 평면 `articles[]`
- 기존 `DailyPageResponse.navigation`
- 페이지 단위 아카이브 응답과 날짜 내림차순 정렬

FE는 갱신된 OpenAPI 스펙을 기준으로 타입과 query hook을 변경한다. 스펙과 FE/BE
구현은 같은 배포 단위에서 테스트한 뒤 수정 배포한다.

## 11. 구현 시 책임 분리

- Router: query 선언, 인증, response model
- Service: 공개 버전 선택, 필터 결합, 상태 정책
- Assembler: DB projection을 Pydantic 응답으로 변환
- Repository: SQL, 재귀 테마 조회, snapshot 관계 저장
- Batch provider: Gemini와 Ollama I/O
- Pure validator/scorer: 테마 규칙 검증, lexical 점수, 그룹 생성, 대표 선정
- Evaluation tooling: 운영 코드와 분리된 품질 측정

구현 계획은 이 설계가 문서 상태로 최종 확인된 후 별도로 작성한다.
