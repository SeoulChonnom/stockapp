from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.batch.models import BatchExecutionContext
from app.batch.providers.ollama_embedding_provider import OllamaEmbeddingProvider
from app.batch.steps.group_similar_articles import (
    GroupSimilarArticlesStep,
)
from app.domains.clusters.assembler import build_cluster_detail_payload
from app.domains.pages.assembler import build_daily_page_payload
from tests.support import BUSINESS_DATE


class _BatchRepository:
    session = object()

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.checkpoint: dict[str, object] = {}

    async def add_event(self, **payload: object) -> None:
        self.events.append(payload)

    async def get_job_by_id(self, _job_id: int) -> SimpleNamespace:
        return SimpleNamespace(checkpoint_json=self.checkpoint, lease_token=None)

    async def save_checkpoint(self, *, checkpoint_json: dict[str, object], **_: object):
        self.checkpoint = checkpoint_json
        return True

    async def commit(self) -> None:
        return None


class _ClusterRepository:
    session = object()

    def __init__(self, clusters, memberships, articles, counts) -> None:
        self.clusters = clusters
        self.memberships = memberships
        self.articles = articles
        self.counts = counts
        self.groupings: dict[int, SimpleNamespace] = {}

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
                exact_duplicate_count=self.counts[article_id],
            )
            for article_id in article_ids
        ]

    async def get_cluster_grouping(self, cluster_id):
        return self.groupings.get(cluster_id)


class _PersistedGroupingRepository:
    def __init__(self, cluster_repo: _ClusterRepository) -> None:
        self.cluster_repo = cluster_repo
        self.algorithm_version_by_cluster: dict[int, str] = {}

    async def replace_cluster_groups(
        self, cluster_id, result, *, algorithm_version, exact_counts
    ) -> None:
        generated_at = datetime(2026, 8, 14, 8, 0, tzinfo=UTC)
        groups = []
        members = []
        for group in result.groups:
            group_id = cluster_id * 100 + group.group_rank
            group_members = tuple(
                SimpleNamespace(
                    similar_group_id=group_id,
                    processed_article_id=member.processed_article_id,
                    similarity_score=member.similarity_score,
                    exact_duplicate_count=exact_counts[member.processed_article_id],
                    is_representative=member.is_representative,
                    article_rank=member.article_rank,
                )
                for member in group.members
            )
            groups.append(
                SimpleNamespace(
                    similar_group_id=group_id,
                    cluster_id=cluster_id,
                    group_rank=group.group_rank,
                    representative_article_id=group.representative_article_id,
                    algorithm_version=algorithm_version,
                    generated_at=generated_at,
                    members=group_members,
                )
            )
            members.extend(group_members)
        self.cluster_repo.groupings[cluster_id] = SimpleNamespace(
            status='READY',
            generated_at=generated_at,
            issue_code=None,
            algorithm_version=algorithm_version,
            groups=tuple(groups),
            members=tuple(members),
        )
        self.algorithm_version_by_cluster[cluster_id] = algorithm_version

    async def mark_grouping_unavailable_with_singletons(
        self, cluster_id, articles, exact_counts, algorithm_version
    ) -> None:
        generated_at = None
        groups = []
        members = []
        for group_rank, article in enumerate(articles, start=1):
            article_id = article['processed_article_id']
            group_id = cluster_id * 100 + group_rank
            member = SimpleNamespace(
                similar_group_id=group_id,
                processed_article_id=article_id,
                similarity_score=1.0,
                exact_duplicate_count=exact_counts[article_id],
                is_representative=True,
                article_rank=1,
            )
            groups.append(
                SimpleNamespace(
                    similar_group_id=group_id,
                    cluster_id=cluster_id,
                    group_rank=group_rank,
                    representative_article_id=article_id,
                    algorithm_version=algorithm_version,
                    generated_at=datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
                    members=(member,),
                )
            )
            members.append(member)
        self.cluster_repo.groupings[cluster_id] = SimpleNamespace(
            status='UNAVAILABLE',
            generated_at=generated_at,
            issue_code='SIMILARITY_GROUPING_FAILED',
            algorithm_version=algorithm_version,
            groups=tuple(groups),
            members=tuple(members),
        )
        self.algorithm_version_by_cluster[cluster_id] = algorithm_version


def _article(article_id: int, title: str, summary: str) -> dict[str, object]:
    return {
        'id': article_id,
        'canonical_title': title,
        'source_summary': summary,
        'article_body_excerpt': summary,
        'publisher_name': '통신사',
        'origin_link': f'https://example.test/{article_id}',
        'naver_link': None,
        'published_at': datetime(2026, 8, 14, 7, 0, tzinfo=UTC),
    }


def _build_fixture():
    clusters = [
        {
            'id': 1,
            'cluster_uid': 'cluster-ready',
            'business_date': BUSINESS_DATE,
            'market_type': 'US',
            'cluster_rank': 1,
            'title': 'Acme revenue event',
            'summary_short': 'Acme revenue event',
            'summary_long': None,
            'tags_json': [],
            'representative_article_id': 101,
            'article_count': 3,
            'last_updated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        },
        {
            'id': 2,
            'cluster_uid': 'cluster-singleton',
            'business_date': BUSINESS_DATE,
            'market_type': 'US',
            'cluster_rank': 2,
            'title': 'Singleton event',
            'summary_short': 'Singleton event',
            'summary_long': None,
            'tags_json': [],
            'representative_article_id': 201,
            'article_count': 1,
            'last_updated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        },
        {
            'id': 3,
            'cluster_uid': 'cluster-failure',
            'business_date': BUSINESS_DATE,
            'market_type': 'US',
            'cluster_rank': 3,
            'title': 'Forced provider failure',
            'summary_short': 'Forced provider failure',
            'summary_long': None,
            'tags_json': [],
            'representative_article_id': 301,
            'article_count': 2,
            'last_updated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        },
    ]
    memberships = {
        1: [
            {'processed_article_id': 101, 'article_rank': 1},
            {'processed_article_id': 102, 'article_rank': 2},
            {'processed_article_id': 103, 'article_rank': 3},
        ],
        2: [{'processed_article_id': 201, 'article_rank': 1}],
        3: [
            {'processed_article_id': 301, 'article_rank': 1},
            {'processed_article_id': 302, 'article_rank': 2},
        ],
    }
    articles = {
        101: _article(
            101,
            'Acme reports 10% revenue rise on 2026-08-14',
            'Acme revenue rise',
        ),
        102: _article(
            102,
            'Acme revenue rises 10 percent on 2026-08-14',
            'Acme revenue rise',
        ),
        103: _article(
            103,
            'Acme reports 10% revenue fall on 2026-08-14',
            'Acme revenue fall',
        ),
        201: _article(201, 'Singleton event', 'Singleton event'),
        301: _article(301, 'Forced provider failure A', 'Forced provider failure'),
        302: _article(302, 'Forced provider failure B', 'Forced provider failure'),
    }
    return clusters, memberships, articles


@pytest.mark.anyio
async def test_mock_provider_persists_cluster_isolated_grouping_to_public_reads():
    clusters, memberships, articles = _build_fixture()
    cluster_repo = _ClusterRepository(
        clusters,
        memberships,
        articles,
        {101: 2, 102: 0, 103: 0, 201: 4, 301: 1, 302: 0},
    )
    group_repo = _PersistedGroupingRepository(cluster_repo)
    requests: list[dict[str, object]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload['input']
        if any('forced provider failure' in value for value in inputs):
            return httpx.Response(503, request=request)
        vectors = []
        for value in inputs:
            vectors.append([0.0, 1.0] if 'singleton' in value else [1.0, 0.0])
        return httpx.Response(200, json={'embeddings': vectors}, request=request)

    settings = SimpleNamespace(
        ollama_base_url='http://mock-ollama.invalid',
        ollama_embed_model='bge-m3',
        ollama_timeout_seconds=1.0,
        ollama_max_retries=0,
        similarity_input_chars=2048,
        similarity_threshold=0.45,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        provider = OllamaEmbeddingProvider(settings, client=client)
        step = GroupSimilarArticlesStep(
            cluster_repo_factory=lambda _session: cluster_repo,
            group_repo_factory=lambda _session: group_repo,
            embedding_provider_factory=lambda: provider,
            settings=settings,
        )
        context = await step.run(
            _BatchRepository(),
            BatchExecutionContext(
                job_id=900,
                business_date=BUSINESS_DATE,
                force_run=False,
                rebuild_page_only=False,
            ),
        )

    assert len(requests) == 3
    assert [len(request['input']) for request in requests] == [3, 1, 2]
    assert set(cluster_repo.groupings) == {1, 2, 3}
    assert cluster_repo.groupings[1].status == 'READY'
    assert [
        [member.processed_article_id for member in group.members]
        for group in cluster_repo.groupings[1].groups
    ] == [[101, 102], [103]]
    assert cluster_repo.groupings[2].status == 'READY'
    assert [
        member.processed_article_id for member in cluster_repo.groupings[2].members
    ] == [201]
    assert cluster_repo.groupings[3].status == 'UNAVAILABLE'
    assert [
        member.processed_article_id for member in cluster_repo.groupings[3].members
    ] == [
        301,
        302,
    ]
    assert context.partial_reasons == []
    assert all(
        not hasattr(member, 'vector')
        for grouping in cluster_repo.groupings.values()
        for member in grouping.members
    )
    assert 'vector' not in Path('db/schema_postgresql.sql').read_text().lower()

    ready_grouping = cluster_repo.groupings[1]
    cluster_payload = build_cluster_detail_payload(
        clusters[0],
        articles[101],
        [articles[101], articles[102], articles[103]],
        article_grouping=ready_grouping,
    )
    assert cluster_payload['articleGrouping'] == {
        'status': 'READY',
        'generatedAt': '2026-08-14T08:00:00Z',
        'issue': None,
    }
    assert [
        article['exactDuplicateCount'] for article in cluster_payload['articles']
    ] == [
        2,
        0,
        0,
    ]
    assert [article['similarGroupId'] for article in cluster_payload['articles']] == [
        'sim-cluster-ready-1',
        'sim-cluster-ready-1',
        'sim-cluster-ready-2',
    ]

    failed_grouping = cluster_repo.groupings[3]
    failed_payload = build_cluster_detail_payload(
        clusters[2],
        articles[301],
        [articles[301], articles[302]],
        article_grouping=failed_grouping,
    )
    assert failed_payload['articleGrouping'] == {
        'status': 'UNAVAILABLE',
        'generatedAt': None,
        'issue': {
            'code': 'SIMILARITY_GROUPING_FAILED',
            'message': '유사 기사 묶음을 생성하지 못했습니다.',
        },
    }
    assert [
        article['exactDuplicateCount'] for article in failed_payload['articles']
    ] == [
        1,
        0,
    ]

    page = {
        'id': 1,
        'business_date': BUSINESS_DATE,
        'version_no': 1,
        'page_title': 'Daily',
        'status': 'READY',
        'global_headline': None,
        'generated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        'partial_message': None,
        'raw_news_count': 6,
        'processed_news_count': 6,
        'cluster_count': 3,
        'last_updated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
        'metadata_json': {},
    }
    market = {
        'id': 10,
        'display_order': 1,
        'market_type': 'US',
        'market_label': '미국',
        'summary_title': None,
        'summary_body': None,
        'analysis_background_json': [],
        'analysis_key_themes_json': [],
        'analysis_outlook': None,
        'raw_news_count': 6,
        'processed_news_count': 6,
        'cluster_count': 3,
        'last_updated_at': datetime(2026, 8, 14, 8, 0, tzinfo=UTC),
    }
    page_clusters = [
        {
            'page_market_id': 10,
            'cluster_uid': cluster['cluster_uid'],
            'title': cluster['title'],
            'summary': cluster['summary_short'],
            'article_count': cluster['article_count'],
            'tags_json': [],
            'representative_title': articles[cluster['representative_article_id']][
                'canonical_title'
            ],
            'representative_publisher_name': '통신사',
            'representative_published_at': articles[
                cluster['representative_article_id']
            ]['published_at'],
            'representative_origin_link': articles[
                cluster['representative_article_id']
            ]['origin_link'],
            'representative_naver_link': None,
        }
        for cluster in clusters
    ]
    article_links = []
    for cluster in clusters:
        grouping = cluster_repo.groupings[cluster['id']]
        for group in grouping.groups:
            for member in group.members:
                article = articles[member.processed_article_id]
                article_links.append(
                    {
                        'page_market_id': 10,
                        'processed_article_id': member.processed_article_id,
                        'cluster_uid': cluster['cluster_uid'],
                        'cluster_title': cluster['title'],
                        'title': article['canonical_title'],
                        'publisher_name': article['publisher_name'],
                        'published_at': article['published_at'],
                        'origin_link': article['origin_link'],
                        'naver_link': None,
                        'similar_group_rank': group.group_rank,
                        'is_similar_group_representative': member.is_representative,
                        'exact_duplicate_count': member.exact_duplicate_count,
                    }
                )
    page_payload = build_daily_page_payload(
        page,
        [market],
        [],
        page_clusters,
        article_links,
        neighbors={'previous_business_date': None, 'next_business_date': None},
        versions=[
            {
                'id': 1,
                'version_no': 1,
                'status': 'READY',
                'generated_at': page['generated_at'],
                'is_latest': True,
            }
        ],
    )
    assert page_payload['status'] == 'READY'
    failed_page_articles = [
        article
        for article in page_payload['markets'][0]['articleLinks']
        if article['clusterId'] == 'cluster-failure'
    ]
    assert [article['exactDuplicateCount'] for article in failed_page_articles] == [
        1,
        0,
    ]
    assert [article['similarGroupId'] for article in failed_page_articles] == [
        'sim-cluster-failure-1',
        'sim-cluster-failure-2',
    ]
