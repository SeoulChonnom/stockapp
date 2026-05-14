from enum import StrEnum


class MarketType(StrEnum):
    US = 'US'
    KR = 'KR'


class PageStatus(StrEnum):
    READY = 'READY'
    PARTIAL = 'PARTIAL'
    FAILED = 'FAILED'


class BatchJobStatus(StrEnum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCESS = 'SUCCESS'
    PARTIAL = 'PARTIAL'
    FAILED = 'FAILED'


class BatchTriggerType(StrEnum):
    SCHEDULED = 'SCHEDULED'
    MANUAL = 'MANUAL'
    ADMIN_REBUILD = 'ADMIN_REBUILD'


class AiSummaryStatus(StrEnum):
    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    FALLBACK = 'FALLBACK'


class AiSummaryType(StrEnum):
    GLOBAL_HEADLINE = 'GLOBAL_HEADLINE'
    MARKET_SUMMARY = 'MARKET_SUMMARY'
    CLUSTER_CARD_SUMMARY = 'CLUSTER_CARD_SUMMARY'
    CLUSTER_DETAIL_ANALYSIS = 'CLUSTER_DETAIL_ANALYSIS'


class EventLevel(StrEnum):
    INFO = 'INFO'
    WARN = 'WARN'
    ERROR = 'ERROR'


__all__ = [
    'AiSummaryStatus',
    'AiSummaryType',
    'BatchJobStatus',
    'BatchTriggerType',
    'EventLevel',
    'MarketType',
    'PageStatus',
]
