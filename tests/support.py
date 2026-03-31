from __future__ import annotations

import importlib
import importlib.util
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

KST = timezone(timedelta(hours=9))
BUSINESS_DATE = date(2026, 3, 17)
GENERATED_AT = datetime(2026, 3, 18, 6, 12, 10, tzinfo=timezone.utc)
UPDATED_AT = datetime(2026, 3, 18, 6, 20, 0, tzinfo=timezone.utc)
CLUSTER_UID = "51f0d9a0-9fc5-4f15-a4f9-62856f128683"
SECOND_CLUSTER_UID = "7b9845f6-5c3d-4f2c-a81d-8dcb0b5dd6d2"


def load_module(name: str):
    try:
        spec = importlib.util.find_spec(name)
    except ModuleNotFoundError:
        spec = None
    if spec is None:
        pytest.skip(f"{name} is not available yet", allow_module_level=True)
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        requested_root = name.split(".")[0]
        missing_root = exc.name.split(".")[0]
        if missing_root == requested_root:
            pytest.skip(f"{name} is not available yet", allow_module_level=True)
        raise


def jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


def normalize_sql(statement: Any) -> str:
    if hasattr(statement, "compile"):
        try:
            compiled = statement.compile(compile_kwargs={"literal_binds": True})
            return " ".join(str(compiled).split())
        except Exception:
            pass
    return " ".join(str(statement).split())


def build_representative_article() -> dict[str, Any]:
    return {
        "title": "엔비디아 급등에 반도체 강세",
        "publisherName": "매일경제",
        "publishedAt": "2026-03-17T23:15:00+00:00",
        "originLink": "https://example.com/article1",
        "naverLink": "https://search.naver.com/article1",
    }


def build_related_articles() -> list[dict[str, Any]]:
    return [
        {
            "title": "엔비디아 강세에 반도체 섹터 동반 상승",
            "publisherName": "연합뉴스",
            "publishedAt": "2026-03-17T22:40:00+00:00",
            "originLink": "https://example.com/article2",
            "naverLink": "https://search.naver.com/article2",
        },
        {
            "title": "대형 기술주 재평가로 나스닥 반등",
            "publisherName": "한국경제",
            "publishedAt": "2026-03-17T21:55:00+00:00",
            "originLink": "https://example.com/article3",
            "naverLink": "https://search.naver.com/article3",
        },
    ]


def build_daily_page_payload() -> dict[str, Any]:
    return {
        "pageId": 501,
        "businessDate": "2026-03-17",
        "versionNo": 3,
        "pageTitle": "글로벌 시장 일간 요약 - 2026-03-17",
        "status": "READY",
        "globalHeadline": "기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세",
        "generatedAt": "2026-03-18T06:12:10+00:00",
        "partialMessage": None,
        "markets": [
            {
                "marketType": "US",
                "marketLabel": "미국 증시 일간 요약",
                "summaryTitle": "반도체와 대형 성장주가 시장 주도권을 회복",
                "summaryBody": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
                "analysis": {
                    "background": ["대형 기술주 매수세 유입", "금리 우려는 잔존"],
                    "keyThemes": ["AI", "반도체", "금리"],
                    "outlook": "다음 거래일에는 CPI 발표와 대형주 실적이 중요 변수다.",
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
                    }
                ],
                "topClusters": [
                    {
                        "clusterId": CLUSTER_UID,
                        "title": "엔비디아 및 반도체 강세에 기술주 상승",
                        "summary": "반도체 업종 강세가 나스닥 상승을 견인했다.",
                        "articleCount": 6,
                        "tags": ["반도체", "AI", "나스닥"],
                        "representativeArticle": build_representative_article(),
                    }
                ],
                "articleLinks": [
                    {
                        "processedArticleId": 2001,
                        "clusterId": CLUSTER_UID,
                        "clusterTitle": "엔비디아 및 반도체 강세에 기술주 상승",
                        "title": "엔비디아 급등에 반도체 강세",
                        "publisherName": "매일경제",
                        "publishedAt": "2026-03-17T23:15:00+00:00",
                        "originLink": "https://example.com/article1",
                        "naverLink": "https://search.naver.com/article1",
                    },
                    {
                        "processedArticleId": 2002,
                        "clusterId": CLUSTER_UID,
                        "clusterTitle": "엔비디아 및 반도체 강세에 기술주 상승",
                        "title": "엔비디아 강세에 반도체 섹터 동반 상승",
                        "publisherName": "연합뉴스",
                        "publishedAt": "2026-03-17T22:40:00+00:00",
                        "originLink": "https://example.com/article2",
                        "naverLink": "https://search.naver.com/article2",
                    }
                ],
                "metadata": {
                    "rawNewsCount": 85,
                    "processedNewsCount": 26,
                    "clusterCount": 7,
                    "lastUpdatedAt": "2026-03-18T06:12:10+00:00",
                    "partialMessage": None,
                },
            },
            {
                "marketType": "KR",
                "marketLabel": "한국 증시 일간 요약",
                "summaryTitle": "수출주와 대형주 반등으로 코스피 회복",
                "summaryBody": "외국인 순매수와 반도체 업황 기대가 지수 반등을 이끌었다.",
                "analysis": {
                    "background": ["외국인 순매수", "대형 수출주 회복"],
                    "keyThemes": ["반도체", "자동차", "원화"],
                    "outlook": "환율 안정 여부와 미국 기술주 흐름이 다음 세션의 관건이다.",
                },
                "indices": [
                    {
                        "indexCode": "KS11",
                        "indexName": "KOSPI",
                        "closePrice": 2730.55,
                        "changeValue": 21.44,
                        "changePercent": 0.79,
                        "highPrice": 2742.18,
                        "lowPrice": 2711.97,
                    },
                    {
                        "indexCode": "KQ11",
                        "indexName": "KOSDAQ",
                        "closePrice": 912.34,
                        "changeValue": 7.28,
                        "changePercent": 0.8,
                        "highPrice": 918.72,
                        "lowPrice": 905.02,
                    },
                ],
                "topClusters": [
                    {
                        "clusterId": SECOND_CLUSTER_UID,
                        "title": "반도체와 자동차 동반 강세",
                        "summary": "대형 수출주 강세가 코스피 반등을 지지했다.",
                        "articleCount": 5,
                        "tags": ["반도체", "자동차", "코스피"],
                        "representativeArticle": {
                            "title": "수출주 반등에 코스피 상승",
                            "publisherName": "서울경제",
                            "publishedAt": "2026-03-17T22:05:00+00:00",
                            "originLink": "https://example.com/article4",
                            "naverLink": "https://search.naver.com/article4",
                        },
                    }
                ],
                "articleLinks": [
                    {
                        "processedArticleId": 3001,
                        "clusterId": SECOND_CLUSTER_UID,
                        "clusterTitle": "반도체와 자동차 동반 강세",
                        "title": "수출주 반등에 코스피 상승",
                        "publisherName": "서울경제",
                        "publishedAt": "2026-03-17T22:05:00+00:00",
                        "originLink": "https://example.com/article4",
                        "naverLink": "https://search.naver.com/article4",
                    },
                    {
                        "processedArticleId": 3002,
                        "clusterId": SECOND_CLUSTER_UID,
                        "clusterTitle": "반도체와 자동차 동반 강세",
                        "title": "자동차·반도체 동반 상승세",
                        "publisherName": "한국경제",
                        "publishedAt": "2026-03-17T21:20:00+00:00",
                        "originLink": "https://example.com/article5",
                        "naverLink": "https://search.naver.com/article5",
                    }
                ],
                "metadata": {
                    "rawNewsCount": 89,
                    "processedNewsCount": 31,
                    "clusterCount": 6,
                    "lastUpdatedAt": "2026-03-18T06:12:10+00:00",
                    "partialMessage": None,
                },
            },
        ],
        "metadata": {
            "rawNewsCount": 174,
            "processedNewsCount": 114,
            "clusterCount": 21,
            "lastUpdatedAt": "2026-03-18T06:12:10+00:00",
        },
    }


def build_archive_item_payload(page_id: int = 501, business_date: str = "2026-03-17") -> dict[str, Any]:
    return {
        "pageId": page_id,
        "businessDate": business_date,
        "pageTitle": f"글로벌 시장 일간 요약 - {business_date}",
        "headlineSummary": "기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세",
        "status": "READY",
        "generatedAt": "2026-03-18T06:12:10+00:00",
        "partialMessage": None,
    }


def build_archive_list_payload() -> dict[str, Any]:
    return {
        "items": [
            build_archive_item_payload(),
            build_archive_item_payload(page_id=502, business_date="2026-03-16"),
        ],
        "pagination": {"page": 1, "size": 30, "totalCount": 2},
        "summary": {"readyCount": 2, "partialCount": 0, "failedCount": 0},
    }


def build_cluster_detail_payload() -> dict[str, Any]:
    return {
        "clusterId": CLUSTER_UID,
        "businessDate": "2026-03-17",
        "marketType": "US",
        "marketLabel": "미국",
        "title": "엔비디아 및 반도체 강세에 기술주 상승",
        "tags": ["반도체", "AI", "나스닥"],
        "summary": {
            "short": "반도체 업종 강세가 나스닥 상승을 견인했다.",
            "long": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
            "analysis": [
                "대형 기술주 매수세 유입",
                "금리 우려는 잔존",
                "다음 거래일에는 CPI 발표와 대형주 실적이 중요 변수다.",
            ],
        },
        "representativeArticle": build_representative_article(),
        "articles": [
            {
                "title": "엔비디아 급등에 반도체 강세",
                "publisherName": "매일경제",
                "publishedAt": "2026-03-17T23:15:00+00:00",
                "originLink": "https://example.com/article1",
                "naverLink": "https://search.naver.com/article1",
            },
            *build_related_articles(),
        ],
        "lastUpdatedAt": "2026-03-18T06:12:10+00:00",
    }


def build_batch_run_payload() -> dict[str, Any]:
    return {
        "jobId": 1001,
        "jobName": "market_daily_batch",
        "businessDate": "2026-03-17",
        "status": "RUNNING",
        "startedAt": "2026-03-18T06:10:00+00:00",
    }


def build_batch_job_list_payload() -> dict[str, Any]:
    return {
        "items": [
            {
                "jobId": 1001,
                "jobName": "market_daily_batch",
                "businessDate": "2026-03-17",
                "status": "SUCCESS",
                "startedAt": "2026-03-18T06:10:00+00:00",
                "endedAt": "2026-03-18T06:12:15+00:00",
                "durationSeconds": 135,
                "marketScope": "GLOBAL",
                "rawNewsCount": 174,
                "processedNewsCount": 114,
                "clusterCount": 21,
                "pageId": 501,
                "pageVersionNo": 3,
                "partialMessage": None,
            }
        ],
        "pagination": {
            "page": 1,
            "size": 20,
            "totalCount": 1,
        },
        "summary": {
            "successCount": 17,
            "partialCount": 1,
            "failedCount": 0,
            "avgDurationSeconds": 862,
        },
    }


def build_batch_job_detail_payload() -> dict[str, Any]:
    return {
        "jobId": 1001,
        "jobName": "market_daily_batch",
        "businessDate": "2026-03-17",
        "status": "SUCCESS",
        "forceRun": False,
        "rebuildPageOnly": False,
        "startedAt": "2026-03-18T06:10:00+00:00",
        "endedAt": "2026-03-18T06:12:15+00:00",
        "durationSeconds": 135,
        "rawNewsCount": 174,
        "processedNewsCount": 114,
        "clusterCount": 21,
        "pageId": 501,
        "pageVersionNo": 3,
        "partialMessage": None,
        "errorCode": None,
        "errorMessage": None,
        "logSummary": "정상 처리. 시장 데이터, 기사 수집, 클러스터링이 SLA 안에서 종료됐다.",
    }


def build_page_snapshot_row() -> dict[str, Any]:
    return {
        "id": 501,
        "business_date": BUSINESS_DATE,
        "version_no": 3,
        "page_title": "글로벌 시장 일간 요약 - 2026-03-17",
        "status": "READY",
        "global_headline": "기술주 강세와 외국인 매수세 회복으로 미·한 증시 모두 강세",
        "generated_at": GENERATED_AT,
        "partial_message": None,
        "raw_news_count": 174,
        "processed_news_count": 114,
        "cluster_count": 21,
        "last_updated_at": UPDATED_AT,
        "metadata_json": {},
    }


def build_page_market_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 1001,
            "page_id": 501,
            "market_type": "US",
            "display_order": 1,
            "market_label": "미국 증시 일간 요약",
            "summary_title": "반도체와 대형 성장주가 시장 주도권을 회복",
            "summary_body": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
            "analysis_background_json": ["대형 기술주 매수세 유입", "금리 우려는 잔존"],
            "analysis_key_themes_json": ["AI", "반도체", "금리"],
            "analysis_outlook": "다음 거래일에는 CPI 발표와 대형주 실적이 중요 변수다.",
            "raw_news_count": 85,
            "processed_news_count": 26,
            "cluster_count": 7,
            "last_updated_at": UPDATED_AT,
            "partial_message": None,
            "metadata_json": {},
        },
        {
            "id": 1002,
            "page_id": 501,
            "market_type": "KR",
            "display_order": 2,
            "market_label": "한국 증시 일간 요약",
            "summary_title": "수출주와 대형주 반등으로 코스피 회복",
            "summary_body": "외국인 순매수와 반도체 업황 기대가 지수 반등을 이끌었다.",
            "analysis_background_json": ["외국인 순매수", "대형 수출주 회복"],
            "analysis_key_themes_json": ["반도체", "자동차", "원화"],
            "analysis_outlook": "환율 안정 여부와 미국 기술주 흐름이 다음 세션의 관건이다.",
            "raw_news_count": 89,
            "processed_news_count": 31,
            "cluster_count": 6,
            "last_updated_at": UPDATED_AT,
            "partial_message": None,
            "metadata_json": {},
        },
    ]


def build_page_index_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 2001,
            "page_market_id": 1001,
            "display_order": 1,
            "index_code": "^IXIC",
            "index_name": "NASDAQ",
            "close_price": 18250.12,
            "change_value": 120.33,
            "change_percent": 0.66,
            "high_price": 18300.1,
            "low_price": 18100.2,
            "currency_code": "USD",
        },
        {
            "id": 2002,
            "page_market_id": 1002,
            "display_order": 1,
            "index_code": "KS11",
            "index_name": "KOSPI",
            "close_price": 2730.55,
            "change_value": 21.44,
            "change_percent": 0.79,
            "high_price": 2742.18,
            "low_price": 2711.97,
            "currency_code": "KRW",
        },
    ]


def build_page_cluster_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 3001,
            "page_market_id": 1001,
            "display_order": 1,
            "cluster_id": 7001,
            "cluster_uid": CLUSTER_UID,
            "title": "엔비디아 및 반도체 강세에 기술주 상승",
            "summary": "반도체 업종 강세가 나스닥 상승을 견인했다.",
            "article_count": 6,
            "tags_json": ["반도체", "AI", "나스닥"],
            "representative_article_id": 4001,
            "representative_title": "엔비디아 급등에 반도체 강세",
            "representative_publisher_name": "매일경제",
            "representative_published_at": "2026-03-17T23:15:00+00:00",
            "representative_origin_link": "https://example.com/article1",
            "representative_naver_link": "https://search.naver.com/article1",
        }
    ]


def build_page_article_link_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 4001,
            "page_market_id": 1001,
            "display_order": 1,
            "processed_article_id": 4001,
            "cluster_id": 7001,
            "cluster_uid": CLUSTER_UID,
            "cluster_title": "엔비디아 및 반도체 강세에 기술주 상승",
            "title": "엔비디아 급등에 반도체 강세",
            "publisher_name": "매일경제",
            "published_at": "2026-03-17T23:15:00+00:00",
            "origin_link": "https://example.com/article1",
            "naver_link": "https://search.naver.com/article1",
        },
        {
            "id": 4002,
            "page_market_id": 1001,
            "display_order": 2,
            "processed_article_id": 4002,
            "cluster_id": 7001,
            "cluster_uid": CLUSTER_UID,
            "cluster_title": "엔비디아 및 반도체 강세에 기술주 상승",
            "title": "엔비디아 강세에 반도체 섹터 동반 상승",
            "publisher_name": "연합뉴스",
            "published_at": "2026-03-17T22:40:00+00:00",
            "origin_link": "https://example.com/article2",
            "naver_link": "https://search.naver.com/article2",
        }
    ]


def build_cluster_row() -> dict[str, Any]:
    return {
        "id": 7001,
        "cluster_uid": CLUSTER_UID,
        "business_date": BUSINESS_DATE,
        "market_type": "US",
        "cluster_rank": 1,
        "title": "엔비디아 및 반도체 강세에 기술주 상승",
        "summary_short": "반도체 업종 강세가 나스닥 상승을 견인했다.",
        "summary_long": "PPI 둔화 신호와 장기 금리 하락이 나스닥 중심 랠리를 자극했다.",
        "analysis_paragraphs_json": [
            "대형 기술주 매수세 유입",
            "금리 우려는 잔존",
        ],
        "tags_json": ["반도체", "AI", "나스닥"],
        "representative_article_id": 4001,
        "article_count": 6,
        "last_updated_at": UPDATED_AT,
    }


def build_cluster_article_rows() -> list[dict[str, Any]]:
    return [
        {
            "cluster_id": 7001,
            "processed_article_id": 4001,
            "article_rank": 1,
        },
        {
            "cluster_id": 7001,
            "processed_article_id": 4002,
            "article_rank": 2,
        },
        {
            "cluster_id": 7001,
            "processed_article_id": 4003,
            "article_rank": 3,
        },
    ]


def build_processed_article_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": 4001,
            "business_date": BUSINESS_DATE,
            "market_type": "US",
            "dedupe_hash": "a" * 64,
            "canonical_title": "엔비디아 급등에 반도체 강세",
            "publisher_name": "매일경제",
            "published_at": "2026-03-17T23:15:00+00:00",
            "origin_link": "https://example.com/article1",
            "naver_link": "https://search.naver.com/article1",
            "source_summary": "반도체 업종 강세가 나스닥 상승을 견인했다.",
            "article_body_excerpt": "PPI 둔화 신호가 투자심리를 개선했다.",
            "content_json": {},
        },
        {
            "id": 4002,
            "business_date": BUSINESS_DATE,
            "market_type": "US",
            "dedupe_hash": "b" * 64,
            "canonical_title": "엔비디아 강세에 반도체 섹터 동반 상승",
            "publisher_name": "연합뉴스",
            "published_at": "2026-03-17T22:40:00+00:00",
            "origin_link": "https://example.com/article2",
            "naver_link": "https://search.naver.com/article2",
            "source_summary": "대형 기술주 매수세가 확대됐다.",
            "article_body_excerpt": "장기 금리 하락이 성장주에 우호적이었다.",
            "content_json": {},
        },
        {
            "id": 4003,
            "business_date": BUSINESS_DATE,
            "market_type": "US",
            "dedupe_hash": "c" * 64,
            "canonical_title": "대형 기술주 재평가로 나스닥 반등",
            "publisher_name": "한국경제",
            "published_at": "2026-03-17T21:55:00+00:00",
            "origin_link": "https://example.com/article3",
            "naver_link": "https://search.naver.com/article3",
            "source_summary": "금리 우려가 일부 완화됐다.",
            "article_body_excerpt": "시장 참여자들은 다음 경제지표를 주시했다.",
            "content_json": {},
        },
    ]


def build_raw_article_rows() -> list[dict[str, Any]]:
    return [
        {
            "raw_article_id": 1,
            "provider_name": "NAVER_NEWS",
            "provider_article_key": "raw-1",
            "market_type": "US",
            "business_date": BUSINESS_DATE,
            "search_keyword": "엔비디아",
            "title": "<b>엔비디아 급등에 반도체 강세</b>",
            "publisher_name": "매일경제",
            "published_at": "2026-03-17T23:15:00+00:00",
            "origin_link": "https://example.com/article1",
            "naver_link": "https://search.naver.com/article1",
            "payload_json": {"description": "반도체 업종 강세가 나스닥 상승을 견인했다."},
            "collected_at": GENERATED_AT,
            "created_at": GENERATED_AT,
        },
        {
            "raw_article_id": 2,
            "provider_name": "NAVER_NEWS",
            "provider_article_key": "raw-2",
            "market_type": "US",
            "business_date": BUSINESS_DATE,
            "search_keyword": "엔비디아",
            "title": "엔비디아 급등에 반도체 강세",
            "publisher_name": "매일경제",
            "published_at": "2026-03-17T23:16:00+00:00",
            "origin_link": "https://example.com/article1/",
            "naver_link": "https://search.naver.com/article2",
            "payload_json": {"description": "반도체 업종 강세가 나스닥 상승을 견인했다."},
            "collected_at": GENERATED_AT,
            "created_at": GENERATED_AT,
        },
    ]


@dataclass
class DummyScalarResult:
    rows: list[Any]

    def all(self) -> list[Any]:
        return list(self.rows)

    def first(self) -> Any:
        return self.rows[0] if self.rows else None

    def one_or_none(self) -> Any:
        if not self.rows:
            return None
        if len(self.rows) > 1:
            raise AssertionError("expected at most one row")
        return self.rows[0]


@dataclass
class DummyResult:
    rows: list[Any]

    def mappings(self) -> "DummyResult":
        return self

    def one(self) -> Any:
        if not self.rows:
            raise AssertionError("expected one row")
        if len(self.rows) > 1:
            raise AssertionError("expected one row")
        return self.rows[0]

    def one_or_none(self) -> Any:
        if not self.rows:
            return None
        if len(self.rows) > 1:
            raise AssertionError("expected at most one row")
        return self.rows[0]

    def scalar_one_or_none(self) -> Any:
        return self.rows[0] if self.rows else None

    def scalar_one(self) -> Any:
        if not self.rows:
            raise AssertionError("expected one row")
        if len(self.rows) > 1:
            raise AssertionError("expected one row")
        return self.rows[0]

    def scalars(self) -> DummyScalarResult:
        return DummyScalarResult(self.rows)

    def all(self) -> list[Any]:
        return list(self.rows)


class RecordingAsyncSession:
    def __init__(self, results: list[DummyResult] | None = None):
        self.results = list(results or [])
        self.statements: list[Any] = []
        self.parameters: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> DummyResult:
        self.statements.append(statement)
        self.parameters.append(params)
        if self.results:
            return self.results.pop(0)
        return DummyResult([])

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def close(self) -> None:
        return None
