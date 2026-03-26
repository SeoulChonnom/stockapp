from enum import StrEnum


class MarketType(StrEnum):
    US = "US"
    KR = "KR"


class PageStatus(StrEnum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class AiSummaryStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    FALLBACK = "FALLBACK"


class AiSummaryType(StrEnum):
    GLOBAL_HEADLINE = "GLOBAL_HEADLINE"
    MARKET_SUMMARY = "MARKET_SUMMARY"
    CLUSTER_CARD_SUMMARY = "CLUSTER_CARD_SUMMARY"
    CLUSTER_DETAIL_ANALYSIS = "CLUSTER_DETAIL_ANALYSIS"


__all__ = ["AiSummaryStatus", "AiSummaryType", "MarketType", "PageStatus"]
