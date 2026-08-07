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


class BatchRunMode(StrEnum):
    FULL = 'FULL'
    PAGE_REBUILD = 'PAGE_REBUILD'
    AI_RETRY = 'AI_RETRY'
    NEWS_COLLECTION = 'NEWS_COLLECTION'


class BatchJobType(StrEnum):
    NEWS_COLLECTION = 'NEWS_COLLECTION'
    MARKET_SNAPSHOT = 'MARKET_SNAPSHOT'


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


class BatchStepStatus(StrEnum):
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'


def derive_batch_job_type(run_mode: str) -> BatchJobType:
    if run_mode == BatchRunMode.NEWS_COLLECTION.value:
        return BatchJobType.NEWS_COLLECTION
    return BatchJobType.MARKET_SNAPSHOT


__all__ = [
    'AiSummaryStatus',
    'AiSummaryType',
    'BatchJobStatus',
    'BatchJobType',
    'BatchRunMode',
    'BatchStepStatus',
    'BatchTriggerType',
    'EventLevel',
    'MarketType',
    'PageStatus',
    'derive_batch_job_type',
]
