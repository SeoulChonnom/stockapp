from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.batch.models import BatchExecutionContext
from app.batch.steps.build_clusters import BuildClustersStep
from app.batch.steps.generate_ai_summaries import GenerateAiSummariesStep
from app.batch.steps.target_progress import TargetCall, run_target_calls
from app.core.llm import LlmRetryableError
from app.db.repositories.projections import NewsArticleProcessedRecord

BUSINESS_DATE = date(2026, 7, 30)
LEASE_TOKEN = UUID('00000000-0000-0000-0000-000000000123')


class DurableRepository:
    def __init__(self) -> None:
        self.session = object()
        self.checkpoint: dict = {'completedSteps': ['DEDUPE_ARTICLES']}
        self.events: list[dict] = []
        self.commit_count = 0

    async def get_job_by_id(self, _job_id):
        return SimpleNamespace(
            checkpoint_json=self.checkpoint,
            lease_token=LEASE_TOKEN,
        )

    async def save_checkpoint(self, *, checkpoint_json, **_kwargs):
        self.checkpoint = checkpoint_json
        return True

    async def add_event(self, *, message, **kwargs):
        self.events.append({'message': message, **kwargs})

    async def commit(self):
        self.commit_count += 1


def _context_from_repository(repository: DurableRepository) -> BatchExecutionContext:
    return BatchExecutionContext.from_checkpoint(
        repository.checkpoint.get('context'),
        job_id=1001,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )


@pytest.mark.anyio
async def test_target_runner_awaits_cancelled_siblings_and_keeps_late_success():
    cancelled = asyncio.Event()
    persisted: list[str] = []

    async def fail():
        await asyncio.sleep(0)
        raise LlmRetryableError()

    async def cancellation_resistant_success():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            return {'status': 'SUCCESS'}

    with pytest.raises(LlmRetryableError):
        await run_target_calls(
            [
                TargetCall('failed', fail),
                TargetCall('late-success', cancellation_resistant_success),
            ],
            on_result=lambda target, _payload: _record_target(persisted, target),
        )

    assert cancelled.is_set()
    assert persisted == ['late-success']


async def _record_target(targets: list[str], target: str) -> None:
    targets.append(target)


@pytest.mark.anyio
async def test_generate_summaries_restart_skips_successes_and_persists_final_fallback(
    monkeypatch,
):
    import app.batch.steps.generate_ai_summaries as module

    calls = {'global': 0, 'market': 0, 'card': 0, 'detail': 0}
    global_done = asyncio.Event()
    detail_done = asyncio.Event()
    card_cancelled = asyncio.Event()

    async def global_summary(*_args, **_kwargs):
        calls['global'] += 1
        global_done.set()
        return _success_payload('global')

    async def market_summary(*_args, **_kwargs):
        calls['market'] += 1
        if calls['market'] == 1:
            await global_done.wait()
            await detail_done.wait()
            raise LlmRetryableError(retry_after_seconds=1)
        return _fallback_payload('market')

    async def card_summary(*_args, **_kwargs):
        calls['card'] += 1
        if calls['card'] == 1:
            try:
                await asyncio.Event().wait()
            finally:
                card_cancelled.set()
        return _success_payload('card')

    async def detail_summary(*_args, **_kwargs):
        calls['detail'] += 1
        detail_done.set()
        return _success_payload('detail')

    monkeypatch.setattr(module, '_generate_global_outputs', global_summary)
    monkeypatch.setattr(module, '_generate_market_summary', market_summary)
    monkeypatch.setattr(module, '_generate_cluster_card_summary', card_summary)
    monkeypatch.setattr(module, '_generate_cluster_detail_summary', detail_summary)

    repository = DurableRepository()
    summary_repo = SummaryRepository()
    step = GenerateAiSummariesStep(
        cluster_repo_factory=lambda _session: SummaryClusterRepository(),
        index_repo_factory=lambda _session: EmptyIndexRepository(),
        summary_repo_factory=lambda _session: summary_repo,
        llm_provider_factory=lambda: SimpleNamespace(concurrency_limit=4),
    )

    with pytest.raises(LlmRetryableError):
        await step.run(repository, _context_from_repository(repository))

    assert card_cancelled.is_set()
    assert set(summary_repo.rows) == {
        'GLOBAL_HEADLINE',
        'CLUSTER_DETAIL_ANALYSIS:7001',
    }

    resumed_context = await step.run(
        repository,
        _context_from_repository(repository),
    )

    assert calls == {'global': 1, 'market': 2, 'card': 2, 'detail': 1}
    assert len(summary_repo.rows) == 4
    assert resumed_context.generated_summary_count == 4
    assert resumed_context.ai_success_count == 3
    assert resumed_context.ai_fallback_count == 1
    assert resumed_context.ai_attempted_count == 4


class SummaryClusterRepository:
    async def list_clusters_by_business_date(self, _business_date):
        return [
            {
                'id': 7001,
                'market_type': 'US',
                'title': 'cluster',
                'summary_short': 'short',
                'summary_long': 'long',
                'analysis_paragraphs_json': [],
                'tags_json': [],
            }
        ]

    async def get_cluster_articles(self, _cluster_id):
        return [{'processed_article_id': 4001}]

    async def get_processed_articles(self, _article_ids):
        return []


class EmptyIndexRepository:
    async def list_indices_by_business_date(self, _business_date):
        return []


class SummaryRepository:
    def __init__(self) -> None:
        self.rows: dict[str, object] = {}

    async def upsert_full_run_summary(self, params):
        self.rows[params.target_key] = params
        return params

    async def upsert_retry_summary(self, params):
        raise AssertionError('normal batch must not use retry upsert')


def _success_payload(label: str) -> dict:
    return {
        'title': label,
        'body': label,
        'status': 'SUCCESS',
        'fallback_used': False,
        'metadata_json': {'reason': 'llm'},
    }


def _fallback_payload(label: str) -> dict:
    return {
        'title': label,
        'body': label,
        'status': 'FALLBACK',
        'fallback_used': True,
        'error_message': 'AI provider request failed; fallback content was used.',
        'metadata_json': {'reason': 'llm_fallback'},
    }


@pytest.mark.anyio
async def test_build_clusters_restart_skips_persisted_enrichment_and_awaits_cancel(
    monkeypatch,
):
    import app.batch.steps.build_clusters as module

    articles = [
        _processed_article(4001),
        _processed_article(4002),
        _processed_article(4003),
    ]
    monkeypatch.setattr(
        module,
        '_rank_market_clusters',
        lambda _clusters: [[article] for article in articles],
    )
    repository = DurableRepository()
    cluster_repo = ClusterWriteRepository()
    provider = RetryingClusterProvider()
    step = BuildClustersStep(
        processed_repo_factory=lambda _session: ProcessedRepository(articles),
        cluster_repo_factory=lambda _session: cluster_repo,
        llm_provider_factory=lambda: provider,
        settings=SimpleNamespace(
            batch_max_clusters_per_market=12,
            batch_clustering_processed_article_limit=5000,
            batch_max_articles_per_cluster=60,
        ),
    )

    with pytest.raises(LlmRetryableError):
        await step.run(repository, _context_from_repository(repository))

    assert provider.cancelled.is_set()
    assert set(cluster_repo.rows) == {1}
    assert repository.checkpoint['targetProgress']['BUILD_CLUSTERS'] == ['US:1']

    resumed_context = await step.run(
        repository,
        _context_from_repository(repository),
    )

    assert provider.calls == {4001: 1, 4002: 2, 4003: 2}
    assert set(cluster_repo.rows) == {1, 2, 3}
    assert repository.commit_count == 3
    assert resumed_context.cluster_count == 3


class ProcessedRepository:
    def __init__(self, articles):
        self.articles = articles

    async def list_by_business_date(
        self, _business_date, *, market_type=None, limit=None
    ):
        _ = (market_type, limit)
        return self.articles


class ClusterWriteRepository:
    def __init__(self) -> None:
        self.rows: dict[int, object] = {}
        self.theme_rows: dict[int, list[object]] = {
            1: [SimpleNamespace(theme_code='old-1', rank=1)],
            2: [SimpleNamespace(theme_code='old-2', rank=1)],
            3: [SimpleNamespace(theme_code='old-3', rank=1)],
        }
        self.theme_replace_calls: list[int] = []

    async def list_cluster_ids_for_business_date(self, *_args, min_rank=None):
        return [
            cluster_id
            for cluster_id in self.rows
            if min_rank is None or cluster_id > min_rank
        ]

    async def delete_clusters_by_ids(self, cluster_ids):
        for cluster_id in cluster_ids:
            self.rows.pop(cluster_id, None)

    async def create_cluster_bundle(self, params, _article_ids):
        row = SimpleNamespace(cluster_id=params.cluster_rank)
        self.rows[params.cluster_rank] = row
        return row

    async def replace_cluster_themes(self, cluster_id, assignments):
        self.theme_replace_calls.append(cluster_id)
        self.theme_rows[cluster_id] = list(assignments)


class RetryingClusterProvider:
    concurrency_limit = 3

    def __init__(self) -> None:
        self.calls = {4001: 0, 4002: 0, 4003: 0}
        self.first_done = asyncio.Event()
        self.cancelled = asyncio.Event()

    def is_configured(self):
        return True

    async def enrich_cluster(self, *, articles, **_kwargs):
        article_id = articles[0]['processedArticleId']
        self.calls[article_id] += 1
        if article_id == 4001:
            self.first_done.set()
        elif article_id == 4002 and self.calls[article_id] == 1:
            await self.first_done.wait()
            raise LlmRetryableError()
        elif article_id == 4003 and self.calls[article_id] == 1:
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()
        return {
            'title': f'cluster {article_id}',
            'summary_short': 'short',
            'summary_long': 'long',
            'tags': [],
            'analysis_paragraphs': [],
            'representative_article_index': 0,
        }


def _processed_article(article_id: int) -> NewsArticleProcessedRecord:
    timestamp = datetime(2026, 7, 30, article_id - 4000, tzinfo=UTC)
    return NewsArticleProcessedRecord(
        processed_article_id=article_id,
        business_date=BUSINESS_DATE,
        market_type='US',
        dedupe_hash=f'{article_id:064x}',
        canonical_title=f'article {article_id}',
        publisher_name='publisher',
        published_at=timestamp,
        origin_link=f'https://example.test/{article_id}',
        naver_link=None,
        source_summary='summary',
        article_body_excerpt='excerpt',
        content_json={},
        created_at=timestamp,
        updated_at=timestamp,
    )
