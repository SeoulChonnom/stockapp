from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from tests.support import load_module

batch_models_module = load_module("app.batch.models")
steps_module = load_module("app.batch.steps")
projections_module = load_module("app.db.repositories.projections")

BatchExecutionContext = batch_models_module.BatchExecutionContext
BuildPageSnapshotStep = steps_module.BuildPageSnapshotStep
CollectMarketIndicesStep = steps_module.CollectMarketIndicesStep
GenerateAiSummariesStep = steps_module.GenerateAiSummariesStep
AiSummaryRecord = projections_module.AiSummaryRecord
MarketIndexDailyRecord = projections_module.MarketIndexDailyRecord


class BoundSession:
    bind = object()

    async def commit(self):
        return None


@dataclass
class EventRepository:
    session: BoundSession
    events: list[tuple[str, str]]

    async def add_event(self, *, step_code: str, message: str, **kwargs):
        _ = kwargs
        self.events.append((step_code, message))


def build_context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=1001,
        business_date=date(2026, 3, 17),
        force_run=False,
        rebuild_page_only=False,
    )


@pytest.mark.anyio
async def test_collect_market_indices_step_populates_market_index_counts(monkeypatch):
    collect_module = load_module("app.batch.steps.collect_market_indices")

    class FakeProvider:
        async def fetch_for_business_date(self, business_date):
            _ = business_date
            return [
                load_module("app.batch.providers.market_index_provider").MarketIndexFetchResult(
                    market_type="US",
                    index_code="^IXIC",
                    index_name="NASDAQ",
                    currency_code="USD",
                    source_date=date(2026, 3, 17),
                    close_price=Decimal("18250.1200"),
                    change_value=Decimal("120.3300"),
                    change_percent=Decimal("0.6600"),
                    high_price=Decimal("18300.1000"),
                    low_price=Decimal("18100.2000"),
                )
            ]

    class FakeRepo:
        def __init__(self, session):
            _ = session
            self.calls = []

        async def upsert_index(self, params):
            self.calls.append(params)

    fake_repo = FakeRepo(BoundSession())
    monkeypatch.setattr(collect_module, "MarketIndexProvider", FakeProvider)
    monkeypatch.setattr(collect_module, "MarketIndexRepository", lambda session: fake_repo)

    repository = EventRepository(session=BoundSession(), events=[])
    context = build_context()

    updated_context = await CollectMarketIndicesStep().run(repository, context)

    assert updated_context.collected_index_count == 1
    assert updated_context.log_messages[-1].startswith("Collected 1 market index")
    assert len(fake_repo.calls) == 1


@pytest.mark.anyio
async def test_generate_ai_summaries_step_records_ai_summary_outputs(monkeypatch):
    generate_module = load_module("app.batch.steps.generate_ai_summaries")

    class FakeClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    "id": 7001,
                    "market_type": "US",
                    "title": "엔비디아 강세",
                    "summary_short": "반도체 강세가 지수를 견인했다.",
                    "summary_long": "엔비디아와 반도체 섹터가 시장 반등을 이끌었다.",
                    "analysis_paragraphs_json": ["반도체 강세", "금리 안정"],
                    "tags_json": ["반도체", "AI"],
                }
                ]

        async def get_cluster_articles(self, cluster_id):
            _ = cluster_id
            return [
                {"processed_article_id": 4001, "article_rank": 1},
                {"processed_article_id": 4002, "article_rank": 2},
            ]

        async def get_processed_articles(self, article_ids):
            _ = article_ids
            return [
                {
                    "id": 4001,
                    "canonical_title": "엔비디아 급등",
                    "publisher_name": "매일경제",
                    "published_at": "2026-03-17T23:15:00+00:00",
                    "origin_link": "https://example.com/article1",
                    "naver_link": "https://search.naver.com/article1",
                    "source_summary": "반도체 강세가 지수를 견인했다.",
                    "article_body_excerpt": "반도체 강세",
                },
                {
                    "id": 4002,
                    "canonical_title": "나스닥 반등",
                    "publisher_name": "한국경제",
                    "published_at": "2026-03-17T22:10:00+00:00",
                    "origin_link": "https://example.com/article2",
                    "naver_link": "https://search.naver.com/article2",
                    "source_summary": "기술주 매수세 확대",
                    "article_body_excerpt": "기술주 강세",
                },
            ]

    class FakeIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return [
                MarketIndexDailyRecord(
                    market_index_daily_id=3001,
                    business_date=date(2026, 3, 17),
                    market_type="US",
                    index_code="^IXIC",
                    index_name="NASDAQ",
                    close_price=Decimal("18250.1200"),
                    change_value=Decimal("120.3300"),
                    change_percent=Decimal("0.6600"),
                    high_price=Decimal("18300.1000"),
                    low_price=Decimal("18100.2000"),
                    currency_code="USD",
                    provider_name="YFINANCE",
                )
            ]

    class FakeSummaryRepo:
        def __init__(self, session):
            _ = session
            self.rows = []

        async def insert_summary(self, params):
            self.rows.append(params)

    class FakeLlmProvider:
        def is_configured(self):
            return False

    fake_summary_repo = FakeSummaryRepo(BoundSession())
    monkeypatch.setattr(generate_module, "ClusterRepository", FakeClusterRepo)
    monkeypatch.setattr(generate_module, "MarketIndexRepository", FakeIndexRepo)
    monkeypatch.setattr(generate_module, "AiSummaryWriteRepository", lambda session: fake_summary_repo)
    monkeypatch.setattr(generate_module, "BatchLlmProvider", FakeLlmProvider)

    repository = EventRepository(session=BoundSession(), events=[])
    context = build_context()
    context.cluster_count = 1

    updated_context = await GenerateAiSummariesStep().run(repository, context)

    assert updated_context.generated_summary_count == 4
    assert updated_context.log_messages[-1].startswith("Generated 4 AI summary")
    assert len(fake_summary_repo.rows) == 4


@pytest.mark.anyio
async def test_build_page_snapshot_step_sets_page_identity_and_writes_snapshot(monkeypatch):
    build_module = load_module("app.batch.steps.build_page_snapshot")

    class FakeClusterRepo:
        def __init__(self, session):
            _ = session

        async def list_clusters_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    "id": 7001,
                    "cluster_uid": UUID("51f0d9a0-9fc5-4f15-a4f9-62856f128683"),
                    "market_type": "US",
                    "cluster_rank": 1,
                    "title": "엔비디아 강세",
                    "summary_short": "반도체 강세가 지수를 견인했다.",
                    "summary_long": "엔비디아와 반도체 섹터가 시장 반등을 이끌었다.",
                    "analysis_paragraphs_json": ["반도체 강세", "금리 안정"],
                    "tags_json": ["반도체", "AI"],
                    "representative_article_id": 4001,
                    "article_count": 2,
                    "representative_title": "엔비디아 급등",
                    "representative_publisher_name": "매일경제",
                    "representative_published_at": datetime(2026, 3, 17, 23, 15, tzinfo=timezone.utc),
                    "representative_origin_link": "https://example.com/article1",
                    "representative_naver_link": "https://search.naver.com/article1",
                }
            ]

        async def list_cluster_article_links_by_business_date(self, business_date):
            _ = business_date
            return [
                {
                    "cluster_id": 7001,
                    "cluster_uid": UUID("51f0d9a0-9fc5-4f15-a4f9-62856f128683"),
                    "market_type": "US",
                    "cluster_rank": 1,
                    "cluster_title": "엔비디아 강세",
                    "processed_article_id": 4001,
                    "article_rank": 1,
                    "title": "엔비디아 급등",
                    "publisher_name": "매일경제",
                    "published_at": datetime(2026, 3, 17, 23, 15, tzinfo=timezone.utc),
                    "origin_link": "https://example.com/article1",
                    "naver_link": "https://search.naver.com/article1",
                },
                {
                    "cluster_id": 7001,
                    "cluster_uid": UUID("51f0d9a0-9fc5-4f15-a4f9-62856f128683"),
                    "market_type": "US",
                    "cluster_rank": 1,
                    "cluster_title": "엔비디아 강세",
                    "processed_article_id": 4002,
                    "article_rank": 2,
                    "title": "나스닥 반등",
                    "publisher_name": "한국경제",
                    "published_at": datetime(2026, 3, 17, 22, 10, tzinfo=timezone.utc),
                    "origin_link": "https://example.com/article2",
                    "naver_link": "https://search.naver.com/article2",
                },
            ]

    class FakeIndexRepo:
        def __init__(self, session):
            _ = session

        async def list_indices_by_business_date(self, business_date):
            _ = business_date
            return [
                MarketIndexDailyRecord(
                    market_index_daily_id=3001,
                    business_date=date(2026, 3, 17),
                    market_type="US",
                    index_code="^IXIC",
                    index_name="NASDAQ",
                    close_price=Decimal("18250.1200"),
                    change_value=Decimal("120.3300"),
                    change_percent=Decimal("0.6600"),
                    high_price=Decimal("18300.1000"),
                    low_price=Decimal("18100.2000"),
                    currency_code="USD",
                    provider_name="YFINANCE",
                )
            ]

    class FakeAiSummaryRepo:
        def __init__(self, session):
            _ = session

        async def list_summaries_for_job(self, job_id):
            _ = job_id
            return [
                AiSummaryRecord(
                    summary_id=1,
                    batch_job_id=1001,
                    summary_type="GLOBAL_HEADLINE",
                    business_date=date(2026, 3, 17),
                    market_type=None,
                    cluster_id=None,
                    title="글로벌 헤드라인",
                    body=None,
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version="v1",
                    status="FALLBACK",
                    fallback_used=True,
                    error_message=None,
                    metadata_json={},
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=timezone.utc),
                ),
                AiSummaryRecord(
                    summary_id=2,
                    batch_job_id=1001,
                    summary_type="MARKET_SUMMARY",
                    business_date=date(2026, 3, 17),
                    market_type="US",
                    cluster_id=None,
                    title="미국 시장 요약",
                    body="기술주 중심 반등",
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version="v1",
                    status="FALLBACK",
                    fallback_used=True,
                    error_message=None,
                    metadata_json={"background": ["반도체 강세"], "keyThemes": ["AI"], "outlook": "지표 주목"},
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=timezone.utc),
                ),
                AiSummaryRecord(
                    summary_id=3,
                    batch_job_id=1001,
                    summary_type="CLUSTER_CARD_SUMMARY",
                    business_date=date(2026, 3, 17),
                    market_type="US",
                    cluster_id=7001,
                    title="엔비디아 강세",
                    body="반도체 강세가 지수를 견인했다.",
                    paragraphs_json=[],
                    model_name=None,
                    prompt_version="v1",
                    status="FALLBACK",
                    fallback_used=True,
                    error_message=None,
                    metadata_json={},
                    generated_at=datetime(2026, 3, 18, 6, 0, tzinfo=timezone.utc),
                ),
            ]

    class FakeSnapshotRepo:
        def __init__(self, session):
            _ = session
            self.calls = []

        async def get_next_version_no(self, business_date):
            self.calls.append(("get_next_version_no", business_date))
            return 4

        async def create_page(self, **kwargs):
            self.calls.append(("create_page", kwargs))
            return 501

        async def create_page_market(self, **kwargs):
            self.calls.append(("create_page_market", kwargs))
            return 1001

        async def insert_page_market_index(self, params):
            self.calls.append(("insert_page_market_index", params))

        async def insert_page_market_cluster(self, params):
            self.calls.append(("insert_page_market_cluster", params))

        async def insert_page_article_link(self, params):
            self.calls.append(("insert_page_article_link", params))

    fake_snapshot_repo = FakeSnapshotRepo(BoundSession())
    monkeypatch.setattr(build_module, "ClusterRepository", FakeClusterRepo)
    monkeypatch.setattr(build_module, "MarketIndexRepository", FakeIndexRepo)
    monkeypatch.setattr(build_module, "AiSummaryRepository", FakeAiSummaryRepo)
    monkeypatch.setattr(build_module, "PageSnapshotWriteRepository", lambda session: fake_snapshot_repo)

    repository = EventRepository(session=BoundSession(), events=[])
    context = build_context()
    context.raw_news_count = 10
    context.processed_news_count = 6
    context.cluster_count = 1

    updated_context = await BuildPageSnapshotStep().run(repository, context)

    assert updated_context.page_id == 501
    assert updated_context.page_version_no == 4
    call_names = [name for name, _payload in fake_snapshot_repo.calls]
    assert "create_page" in call_names
    assert "create_page_market" in call_names
    assert "insert_page_market_cluster" in call_names
    article_link_calls = [payload for name, payload in fake_snapshot_repo.calls if name == "insert_page_article_link"]
    assert len(article_link_calls) == 2
    assert article_link_calls[0]["display_order"] == 1
    assert article_link_calls[0]["processed_article_id"] == 4001
    assert article_link_calls[1]["display_order"] == 2
    assert article_link_calls[1]["processed_article_id"] == 4002
