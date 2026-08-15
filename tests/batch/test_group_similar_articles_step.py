from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest

from app.batch.diagnostics import (
    SIMILAR_GROUP_FAILURE,
    SIMILARITY_GROUPING_FAILED,
)
from app.batch.exceptions import BatchLeaseLostError
from app.batch.models import BatchExecutionContext
from app.batch.providers.ollama_embedding_provider import (
    OllamaEmbeddingError,
    OllamaEmbeddingProvider,
)
from app.batch.steps.group_similar_articles import (
    GROUP_SIMILAR_ARTICLES,
    GroupSimilarArticlesStep,
    build_grouping_algorithm_version,
)
from tests.support import BUSINESS_DATE


def _article(article_id: int, title: str = '같은 사건') -> dict[str, object]:
    return {
        'id': article_id,
        'canonical_title': title,
        'source_summary': '시장 영향과 실적 개선을 전한 기사',
        'article_body_excerpt': '관련 본문 발췌',
        'publisher_name': '테스트뉴스',
        'origin_link': f'https://example.test/{article_id}',
        'published_at': datetime(2026, 8, 14, tzinfo=UTC),
    }


class FakeBatchRepository:
    session = object()

    def __init__(
        self,
        checkpoint: dict | None = None,
        *,
        lease_token: UUID | None = None,
        save_checkpoint_result: bool = True,
    ):
        self.events: list[dict] = []
        self.checkpoint = checkpoint or {}
        self.commits = 0
        self.lease_token = lease_token
        self.save_checkpoint_result = save_checkpoint_result

    async def add_event(self, **payload):
        self.events.append(payload)

    async def get_job_by_id(self, _job_id):
        return SimpleNamespace(
            checkpoint_json=self.checkpoint,
            lease_token=self.lease_token,
        )

    async def save_checkpoint(self, *, checkpoint_json, **_kwargs):
        self.checkpoint = checkpoint_json
        return self.save_checkpoint_result

    async def commit(self):
        self.commits += 1


class FakeClusterRepository:
    def __init__(self, clusters, memberships, articles, counts, groupings=None):
        self.clusters = clusters
        self.memberships = memberships
        self.articles = articles
        self.counts = counts
        self.groupings = groupings or {}

    async def list_clusters_by_business_date(self, _business_date):
        return list(self.clusters)

    async def get_cluster_articles(self, cluster_id):
        return list(self.memberships[cluster_id])

    async def get_processed_articles(self, article_ids):
        return [self.articles[article_id] for article_id in article_ids]

    async def get_exact_duplicate_counts(self, article_ids):
        return [
            SimpleNamespace(
                processed_article_id=article_id,
                exact_duplicate_count=self.counts.get(article_id, 0),
            )
            for article_id in article_ids
        ]

    async def get_cluster_grouping(self, cluster_id):
        return self.groupings.get(cluster_id)


class RecordingGroupRepository:
    def __init__(self):
        self.ready: list[tuple[int, object, str, dict]] = []
        self.unavailable: list[tuple[int, list[object], dict, str]] = []

    async def replace_cluster_groups(
        self, cluster_id, result, *, algorithm_version, exact_counts
    ):
        self.ready.append((cluster_id, result, algorithm_version, dict(exact_counts)))

    async def mark_grouping_unavailable_with_singletons(
        self, cluster_id, articles, exact_counts, algorithm_version
    ):
        self.unavailable.append(
            (cluster_id, list(articles), dict(exact_counts), algorithm_version)
        )

    async def get_cluster_grouping(self, _cluster_id):
        return None


class RecordingEmbeddingProvider:
    def __init__(self, vectors_by_call=None, error: BaseException | None = None):
        self.calls: list[list[object]] = []
        self.vectors_by_call = list(vectors_by_call or [])
        self.error = error

    async def embed_articles(self, articles):
        self.calls.append(list(articles))
        if self.error is not None:
            raise self.error
        return self.vectors_by_call.pop(0)


def _step(cluster_repo, group_repo, provider, *, settings=None):
    return GroupSimilarArticlesStep(
        cluster_repo_factory=lambda _session: cluster_repo,
        group_repo_factory=lambda _session: group_repo,
        embedding_provider_factory=lambda: provider,
        settings=settings
        or SimpleNamespace(
            ollama_embed_model='bge-m3',
            similarity_input_chars=2048,
        ),
    )


def _context() -> BatchExecutionContext:
    return BatchExecutionContext(
        job_id=101,
        business_date=BUSINESS_DATE,
        force_run=False,
        rebuild_page_only=False,
    )


def _repositories(cluster_ids=(1, 2)):
    clusters = [
        {'id': cluster_id, 'market_type': 'US', 'cluster_rank': cluster_id}
        for cluster_id in cluster_ids
    ]
    memberships = {
        cluster_id: [
            {'processed_article_id': cluster_id * 10 + 1, 'article_rank': 1},
            {'processed_article_id': cluster_id * 10 + 2, 'article_rank': 2},
        ]
        for cluster_id in cluster_ids
    }
    articles = {
        article_id: _article(article_id)
        for values in memberships.values()
        for article_id in (row['processed_article_id'] for row in values)
    }
    return (
        FakeClusterRepository(clusters, memberships, articles, {}),
        RecordingGroupRepository(),
    )


@pytest.mark.anyio
async def test_step_orders_clusters_and_calls_provider_once_with_whole_cluster():
    cluster_repo, group_repo = _repositories((2, 1))
    cluster_repo.counts.update({11: 2, 12: 3, 21: 4, 22: 5})
    provider = RecordingEmbeddingProvider(
        vectors_by_call=[[[1.0, 0.0], [1.0, 0.0]], [[1.0, 0.0], [1.0, 0.0]]]
    )
    repository = FakeBatchRepository()

    context = await _step(cluster_repo, group_repo, provider).run(
        repository, _context()
    )

    assert [cluster_id for cluster_id, *_ in group_repo.ready] == [1, 2]
    assert len(provider.calls) == 2
    assert all(len(call) == 2 for call in provider.calls)
    assert [row[3] for row in group_repo.ready] == [
        {11: 2, 12: 3},
        {21: 4, 22: 5},
    ]
    assert context.partial_reasons == []
    assert all(
        event['step_code'] == GROUP_SIMILAR_ARTICLES for event in repository.events
    )


@pytest.mark.anyio
async def test_provider_failure_persists_unavailable_singletons_and_continues():
    cluster_repo, group_repo = _repositories((1, 2))
    cluster_repo.counts.update({11: 6, 12: 7, 21: 8, 22: 9})
    provider = RecordingEmbeddingProvider()
    repository = FakeBatchRepository()

    # The fake fails only the first cluster; the second one is still processed.
    calls = 0

    async def embed_articles(articles):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OllamaEmbeddingError(
                'https://secret.example/article raw article content '
                'vector=[0.1, 0.2] secret-token'
            )
        return [[1.0, 0.0], [1.0, 0.0]]

    provider.embed_articles = embed_articles
    context = await _step(cluster_repo, group_repo, provider).run(
        repository, _context()
    )

    assert [row[0] for row in group_repo.unavailable] == [1]
    assert group_repo.unavailable[0][2] == {11: 6, 12: 7}
    assert [row[0] for row in group_repo.ready] == [2]
    # A step that degraded every cluster used to finish with a silent summary;
    # the reason is a fixed public string, so it carries no provider detail.
    assert context.partial_reasons == [SIMILAR_GROUP_FAILURE['message']]
    assert context.partial_categories == {SIMILARITY_GROUPING_FAILED: 1}
    assert '1 degraded' in context.log_messages[-1]
    event_text = repr(repository.events)
    assert 'https://secret.example/article' not in event_text
    assert 'raw article content' not in event_text
    assert 'vector=[0.1, 0.2]' not in event_text
    assert 'secret-token' not in event_text
    warning = next(event for event in repository.events if event['level'] == 'WARN')
    assert (
        warning['context_json']['error']['code'] == 'EXTERNAL_PROVIDER_REQUEST_FAILED'
    )
    assert warning['context_json']['error']['message'] == (
        'External provider request failed.'
    )
    assert all(
        event['context_json']['error']['message']
        for event in repository.events
        if event['level'] == 'WARN'
    )


@pytest.mark.anyio
async def test_cancelled_provider_error_propagates_without_fallback():
    cluster_repo, group_repo = _repositories((1,))
    provider = RecordingEmbeddingProvider(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await _step(cluster_repo, group_repo, provider).run(
            FakeBatchRepository(), _context()
        )

    assert group_repo.unavailable == []


@pytest.mark.anyio
async def test_database_write_error_propagates_without_fallback():
    cluster_repo, _ = _repositories((1,))

    class FailingGroupRepository(RecordingGroupRepository):
        async def replace_cluster_groups(self, *args, **kwargs):
            raise RuntimeError('database is unavailable')

    provider = RecordingEmbeddingProvider(vectors_by_call=[[[1.0, 0.0], [1.0, 0.0]]])

    with pytest.raises(RuntimeError, match='database'):
        await _step(cluster_repo, FailingGroupRepository(), provider).run(
            FakeBatchRepository(), _context()
        )


@pytest.mark.anyio
async def test_current_algorithm_and_complete_membership_are_checkpoint_done():
    cluster_repo, group_repo = _repositories((1,))
    provider = RecordingEmbeddingProvider()
    algorithm_version = build_grouping_algorithm_version(
        model='bge-m3', input_chars=2048
    )
    cluster_repo.groupings[1] = SimpleNamespace(
        status='READY',
        algorithm_version=algorithm_version,
        members=(
            SimpleNamespace(processed_article_id=11),
            SimpleNamespace(processed_article_id=12),
        ),
    )

    await _step(cluster_repo, group_repo, provider).run(
        FakeBatchRepository(), _context()
    )

    assert provider.calls == []
    assert group_repo.ready == []


@pytest.mark.anyio
async def test_stale_algorithm_or_incomplete_membership_replaces_grouping():
    cluster_repo, group_repo = _repositories((1,))
    provider = RecordingEmbeddingProvider(vectors_by_call=[[[1.0, 0.0], [1.0, 0.0]]])
    cluster_repo.groupings[1] = SimpleNamespace(
        status='READY',
        algorithm_version='old-version',
        members=(SimpleNamespace(processed_article_id=11),),
    )

    await _step(cluster_repo, group_repo, provider).run(
        FakeBatchRepository(), _context()
    )

    assert len(provider.calls) == 1
    assert [row[0] for row in group_repo.ready] == [1]


def test_algorithm_version_distinguishes_close_effective_float_parameters():
    first = build_grouping_algorithm_version(
        model='bge-m3', input_chars=2048, threshold=0.8
    )
    second = build_grouping_algorithm_version(
        model='bge-m3', input_chars=2048, threshold=0.8000001
    )

    assert first != second


def test_algorithm_version_escapes_model_component_delimiters():
    version = build_grouping_algorithm_version(model='bge;m3=private', input_chars=2048)

    assert 'model=bge%3Bm3%3Dprivate;' in version
    assert 'model=bge;m3=private;' not in version


@pytest.mark.anyio
async def test_stale_algorithm_checkpoint_reprocesses_when_threshold_changes():
    cluster_repo, group_repo = _repositories((1,))
    old_version = build_grouping_algorithm_version(
        model='bge-m3', input_chars=2048, threshold=0.8
    )
    cluster_repo.groupings[1] = SimpleNamespace(
        status='READY',
        algorithm_version=old_version,
        members=(
            SimpleNamespace(processed_article_id=11),
            SimpleNamespace(processed_article_id=12),
        ),
    )
    provider = RecordingEmbeddingProvider(vectors_by_call=[[[1.0, 0.0], [1.0, 0.0]]])

    await _step(
        cluster_repo,
        group_repo,
        provider,
        settings=SimpleNamespace(
            ollama_embed_model='bge-m3',
            similarity_input_chars=2048,
            similarity_threshold=0.8000001,
        ),
    ).run(FakeBatchRepository(), _context())

    assert len(provider.calls) == 1
    assert group_repo.ready[0][2] != old_version


@pytest.mark.anyio
async def test_step_uses_provider_input_cap_and_preserves_exact_count_arguments():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(
            200,
            json={'embeddings': [[1.0, 0.0], [1.0, 0.0]]},
            request=request,
        )

    cluster_repo, group_repo = _repositories((1,))
    cluster_repo.counts.update({11: 4, 12: 5})
    settings = SimpleNamespace(
        ollama_base_url='http://ollama.test',
        ollama_embed_model='bge-m3',
        ollama_timeout_seconds=1.0,
        ollama_max_retries=0,
        similarity_input_chars=10,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        await _step(
            cluster_repo,
            group_repo,
            provider,
            settings=settings,
        ).run(FakeBatchRepository(), _context())

    assert len(requests) == 1
    assert requests[0]['model'] == 'bge-m3'
    assert requests[0]['truncate'] is False
    assert len(requests[0]['input']) == 2
    assert all(len(value) <= 10 for value in requests[0]['input'])
    assert group_repo.ready[0][3] == {11: 4, 12: 5}


@pytest.mark.anyio
async def test_lease_loss_while_checkpointing_propagates():
    cluster_repo, group_repo = _repositories((1,))
    provider = RecordingEmbeddingProvider(vectors_by_call=[[[1.0, 0.0], [1.0, 0.0]]])
    repository = FakeBatchRepository(
        lease_token=UUID('00000000-0000-0000-0000-000000000123'),
        save_checkpoint_result=False,
    )

    with pytest.raises(BatchLeaseLostError):
        await _step(cluster_repo, group_repo, provider).run(repository, _context())

    assert len(group_repo.ready) == 1
