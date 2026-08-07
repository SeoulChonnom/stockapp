from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, TypeVar
from uuid import UUID

T = TypeVar('T')


@dataclass(slots=True)
class PagedResult[T]:
    items: Sequence[T]
    page: int
    size: int
    total_count: int


@dataclass(slots=True)
class PageHeaderRecord:
    page_id: int
    business_date: date
    version_no: int
    page_title: str
    status: str
    global_headline: str | None
    generated_at: datetime
    partial_message: str | None = None
    raw_news_count: int | None = None
    processed_news_count: int | None = None
    cluster_count: int | None = None
    last_updated_at: datetime | None = None
    batch_job_id: int | None = None
    metadata_json: Any = None


@dataclass(slots=True)
class PageMarketRecord:
    page_market_id: int
    page_id: int
    market_type: str
    display_order: int
    market_label: str
    summary_title: str | None
    summary_body: str | None
    analysis_background_json: Any
    analysis_key_themes_json: Any
    analysis_outlook: str | None
    raw_news_count: int
    processed_news_count: int
    cluster_count: int
    last_updated_at: datetime
    partial_message: str | None = None
    metadata_json: Any = None
    expected_session_date: date | None = None
    actual_index_source_date: date | None = None
    session_close_at: datetime | None = None
    news_window_start_at: datetime | None = None
    news_window_end_at: datetime | None = None
    news_coverage_complete: bool | None = None


@dataclass(slots=True)
class PageMarketIndexRecord:
    page_market_index_id: int
    page_market_id: int
    market_index_daily_id: int | None
    display_order: int
    index_code: str
    index_name: str
    close_price: Any
    change_value: Any
    change_percent: Any
    high_price: Any | None
    low_price: Any | None
    currency_code: str
    source_date: date | None = None
    expected_session_date: date | None = None
    session_close_at: datetime | None = None


@dataclass(slots=True)
class PageMarketClusterRecord:
    page_market_cluster_id: int
    page_market_id: int
    cluster_id: int | None
    cluster_uid: UUID
    display_order: int
    title: str
    summary: str | None
    article_count: int
    tags_json: Any
    representative_article_id: int | None
    representative_title: str | None
    representative_publisher_name: str | None
    representative_published_at: datetime | None
    representative_origin_link: str | None
    representative_naver_link: str | None


@dataclass(slots=True)
class PageArticleLinkRecord:
    page_article_link_id: int
    page_market_id: int
    display_order: int
    processed_article_id: int | None
    cluster_id: int | None
    cluster_uid: UUID | None
    cluster_title: str | None
    title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str
    naver_link: str | None


@dataclass(slots=True)
class BatchJobSummary:
    success_count: int
    partial_count: int
    failed_count: int
    avg_duration_seconds: int


@dataclass(slots=True)
class BatchJobRecord:
    job_id: int
    job_name: str
    business_date: date
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    market_scope: str
    raw_news_count: int
    processed_news_count: int
    cluster_count: int
    page_id: int | None
    page_version_no: int | None
    partial_message: str | None = None
    trigger_type: str | None = None
    triggered_by_user_id: str | None = None
    force_run: bool | None = None
    rebuild_page_only: bool | None = None
    error_code: str | None = None
    error_message: str | None = None
    log_summary: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    run_mode: str = 'FULL'
    source_job_id: int | None = None
    source_page_id: int | None = None
    idempotency_key: str | None = None
    queued_at: datetime | None = None
    available_at: datetime | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_token: UUID | None = None
    lease_expires_at: datetime | None = None
    heartbeat_at: datetime | None = None
    current_step: str | None = None
    checkpoint_json: Any = None
    ai_target_count: int = 0
    ai_attempted_count: int = 0
    ai_success_count: int = 0
    ai_fallback_count: int = 0
    ai_failed_count: int = 0
    ai_recovered_count: int = 0


@dataclass(slots=True)
class BatchJobCreateParams:
    business_date: date
    status: str
    trigger_type: str
    triggered_by_user_id: str | None
    force_run: bool
    rebuild_page_only: bool
    job_name: str = 'market_daily_batch'
    run_mode: str = 'FULL'
    source_job_id: int | None = None
    source_page_id: int | None = None
    idempotency_key: str | None = None
    max_attempts: int = 3


@dataclass(slots=True)
class BatchPageSource:
    page_id: int
    batch_job_id: int
    status: str


@dataclass(slots=True)
class BatchLeaseRecoveryResult:
    requeued_count: int
    failed_count: int


@dataclass(slots=True)
class BatchJobStepRunRecord:
    step_run_id: int
    step_code: str
    seq: int
    status: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = None


@dataclass(slots=True)
class BatchJobListResult:
    items: Sequence[BatchJobRecord]
    page: int
    size: int
    total_count: int
    summary: BatchJobSummary


@dataclass(slots=True)
class NewsSearchKeywordRecord:
    keyword_id: int
    provider_name: str
    market_type: str
    keyword: str
    is_active: bool
    priority: int
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class NewsSearchKeywordCreateParams:
    provider_name: str
    market_type: str
    keyword: str
    priority: int = 100
    is_active: bool = True


@dataclass(slots=True)
class NewsSearchKeywordUpdateParams:
    keyword: str | None = None
    priority: int | None = None
    is_active: bool | None = None


@dataclass(slots=True)
class NewsCollectionRunRecord:
    run_id: int
    batch_job_id: int
    provider_name: str
    window_start_at: datetime
    window_end_at: datetime
    query_start_at: datetime
    query_end_at: datetime
    total_keyword_count: int
    completed_keyword_count: int
    fetched_count: int
    matched_count: int
    inserted_count: int
    coverage_complete: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class NewsCollectionKeywordDiagnosticParams:
    news_collection_run_id: int
    keyword_id: int
    provider_name: str
    market_type: str
    keyword: str
    status: str
    fetched_count: int
    matched_count: int
    inserted_count: int
    coverage_complete: bool
    error_code: str | None = None
    error_message: str | None = None


@dataclass(slots=True)
class NewsCoverageInterval:
    window_start_at: datetime
    window_end_at: datetime


@dataclass(slots=True)
class NewsArticleRawCreateParams:
    provider_name: str
    provider_article_key: str
    market_type: str
    search_keyword: str | None
    title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str | None
    naver_link: str | None
    payload_json: Any
    business_date: date | None = None


@dataclass(slots=True)
class NewsArticleRawRecord:
    raw_article_id: int
    provider_name: str
    provider_article_key: str
    market_type: str
    business_date: date | None
    search_keyword: str | None
    title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str | None
    naver_link: str | None
    payload_json: Any
    collected_at: datetime
    created_at: datetime


@dataclass(slots=True)
class NewsArticleProcessedCreateParams:
    business_date: date
    market_type: str
    dedupe_hash: str
    canonical_title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str
    naver_link: str | None
    source_summary: str | None
    article_body_excerpt: str | None
    content_json: Any


@dataclass(slots=True)
class NewsArticleProcessedRecord:
    processed_article_id: int
    business_date: date
    market_type: str
    dedupe_hash: str
    canonical_title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str
    naver_link: str | None
    source_summary: str | None
    article_body_excerpt: str | None
    content_json: Any
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class NewsArticleRawProcessedMapCreateParams:
    raw_article_id: int
    processed_article_id: int


@dataclass(slots=True)
class NewsClusterCreateParams:
    business_date: date
    market_type: str
    cluster_rank: int
    title: str
    summary_short: str | None
    summary_long: str | None
    analysis_paragraphs_json: Any
    tags_json: Any
    representative_article_id: int
    article_count: int


@dataclass(slots=True)
class NewsClusterArticleCreateParams:
    cluster_id: int
    processed_article_id: int
    article_rank: int


@dataclass(slots=True)
class NewsClusterWriteRecord:
    cluster_id: int
    cluster_uid: UUID
    cluster_rank: int


@dataclass(slots=True)
class MarketIndexDailyCreateParams:
    business_date: date
    market_type: str
    source_date: date
    expected_session_date: date
    session_close_at: datetime
    index_code: str
    index_name: str
    close_price: Any
    change_value: Any
    change_percent: Any
    high_price: Any | None
    low_price: Any | None
    currency_code: str
    provider_name: str


@dataclass(slots=True)
class MarketIndexDailyRecord:
    market_index_daily_id: int
    business_date: date
    market_type: str
    index_code: str
    index_name: str
    close_price: Any
    change_value: Any
    change_percent: Any
    high_price: Any | None
    low_price: Any | None
    currency_code: str
    provider_name: str
    collected_at: datetime | None = None
    created_at: datetime | None = None
    source_date: date | None = None
    expected_session_date: date | None = None
    session_close_at: datetime | None = None


@dataclass(slots=True)
class BatchJobMarketContextCreateParams:
    batch_job_id: int
    market_type: str
    expected_session_date: date
    session_close_at: datetime
    news_window_start_at: datetime
    news_window_end_at: datetime


@dataclass(slots=True)
class BatchJobMarketContextRecord:
    market_context_id: int
    batch_job_id: int
    market_type: str
    expected_session_date: date
    actual_index_source_date: date | None
    session_close_at: datetime
    news_window_start_at: datetime
    news_window_end_at: datetime
    news_coverage_complete: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class AiSummaryCreateParams:
    batch_job_id: int
    summary_type: str
    business_date: date
    market_type: str | None
    cluster_id: int | None
    title: str | None
    body: str | None
    paragraphs_json: Any
    model_name: str | None
    prompt_version: str | None
    status: str
    fallback_used: bool
    error_message: str | None
    metadata_json: Any
    target_key: str | None = None
    source_summary_id: int | None = None
    attempt_no: int = 1


@dataclass(slots=True)
class ClusterRecord:
    cluster_id: int
    cluster_uid: UUID
    business_date: date
    market_type: str
    cluster_rank: int
    title: str
    summary_short: str | None
    summary_long: str | None
    analysis_paragraphs_json: Any
    tags_json: Any
    representative_article_id: int
    article_count: int
    created_at: datetime
    updated_at: datetime
    representative_processed_article_id: int | None = None
    representative_business_date: date | None = None
    representative_market_type: str | None = None
    representative_dedupe_hash: str | None = None
    representative_canonical_title: str | None = None
    representative_publisher_name: str | None = None
    representative_published_at: datetime | None = None
    representative_origin_link: str | None = None
    representative_naver_link: str | None = None
    representative_source_summary: str | None = None
    representative_article_body_excerpt: str | None = None
    representative_content_json: Any = None


@dataclass(slots=True)
class ClusterArticleRecord:
    cluster_id: int
    processed_article_id: int
    article_rank: int
    is_representative: bool
    business_date: date
    market_type: str
    canonical_title: str
    publisher_name: str | None
    published_at: datetime | None
    origin_link: str
    naver_link: str | None
    source_summary: str | None
    article_body_excerpt: str | None
    content_json: Any


@dataclass(slots=True)
class AiSummaryRecord:
    summary_id: int
    batch_job_id: int
    summary_type: str
    business_date: date
    market_type: str | None
    cluster_id: int | None
    title: str | None
    body: str | None
    paragraphs_json: Any
    model_name: str | None
    prompt_version: str | None
    status: str
    fallback_used: bool
    error_message: str | None
    metadata_json: Any
    generated_at: datetime
    target_key: str | None = None
    source_summary_id: int | None = None
    attempt_no: int = 1
