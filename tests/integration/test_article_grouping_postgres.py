from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.batch.models import BatchExecutionContext
from app.batch.providers.ollama_embedding_provider import OllamaEmbeddingProvider
from app.batch.steps.group_similar_articles import GroupSimilarArticlesStep
from app.db.repositories.article_group_repo import ArticleGroupRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.cluster_repo import ClusterRepository
from tests.support import BUSINESS_DATE

psycopg = pytest.importorskip('psycopg')

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPOSITORY_ROOT / 'db' / 'schema_postgresql.sql'
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / 'db' / 'migrations'


@pytest.fixture
def postgres_dsn():
    dsn = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if dsn is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS stock CASCADE')
        connection.execute(SCHEMA_SQL.read_text(encoding='utf-8'))
        for migration in sorted(MIGRATIONS_DIRECTORY.glob('*.sql')):
            connection.execute(migration.read_text(encoding='utf-8'))
        try:
            yield dsn
        finally:
            connection.execute('DROP SCHEMA IF EXISTS stock CASCADE')


def _article_specs() -> list[dict[str, object]]:
    return [
        {
            'key': 'ready-a',
            'cluster': 'ready',
            'title': 'Acme reports 10% revenue rise on 2026-08-14',
            'summary': 'Acme revenue rise',
            'exact_count': 2,
        },
        {
            'key': 'ready-b',
            'cluster': 'ready',
            'title': 'Acme revenue rises 10 percent on 2026-08-14',
            'summary': 'Acme revenue rise',
            'exact_count': 0,
        },
        {
            'key': 'ready-negative',
            'cluster': 'ready',
            'title': 'Acme reports 10% revenue fall on 2026-08-14',
            'summary': 'Acme revenue fall',
            'exact_count': 0,
        },
        {
            'key': 'singleton',
            'cluster': 'singleton',
            'title': 'Singleton event',
            'summary': 'Singleton event',
            'exact_count': 4,
        },
        {
            'key': 'failure-a',
            'cluster': 'failure',
            'title': 'Forced provider failure A',
            'summary': 'Forced provider failure',
            'exact_count': 1,
        },
        {
            'key': 'failure-b',
            'cluster': 'failure',
            'title': 'Forced provider failure B',
            'summary': 'Forced provider failure',
            'exact_count': 0,
        },
    ]


def _seed_fixture(dsn: str) -> dict[str, object]:
    article_specs = _article_specs()
    with psycopg.connect(dsn) as connection:
        with connection.transaction():
            job_id = connection.execute(
                """
                INSERT INTO stock.batch_job (business_date, status, trigger_type, run_mode)
                VALUES (%s, 'SUCCESS', 'MANUAL', 'FULL')
                RETURNING id
                """,
                (BUSINESS_DATE,),
            ).fetchone()[0]
            page_id = connection.execute(
                """
                INSERT INTO stock.market_daily_page (
                    business_date, version_no, page_title, status, batch_job_id
                )
                VALUES (%s, 1, 'Task 10 integration page', 'READY', %s)
                RETURNING id
                """,
                (BUSINESS_DATE, job_id),
            ).fetchone()[0]

            article_ids: dict[str, int] = {}
            for spec in article_specs:
                key = str(spec['key'])
                title = str(spec['title'])
                summary = str(spec['summary'])
                processed_id = connection.execute(
                    """
                    INSERT INTO stock.news_article_processed (
                        business_date, market_type, dedupe_hash, canonical_title,
                        publisher_name, published_at, origin_link, source_summary,
                        article_body_excerpt
                    )
                    VALUES (%s, 'US', %s, %s, 'Task 10 test publisher', %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        BUSINESS_DATE,
                        sha256(key.encode('utf-8')).hexdigest(),
                        title,
                        datetime(2026, 8, 14, 7, tzinfo=UTC),
                        f'https://example.test/{key}',
                        summary,
                        summary,
                    ),
                ).fetchone()[0]
                article_ids[key] = processed_id
                raw_count = int(spec['exact_count']) + 1
                for raw_index in range(raw_count):
                    raw_id = connection.execute(
                        """
                        INSERT INTO stock.news_article_raw (
                            provider_name, provider_article_key, market_type,
                            business_date, title
                        )
                        VALUES ('TASK10_TEST', %s, 'US', %s, %s)
                        RETURNING id
                        """,
                        (f'{key}-raw-{raw_index}', BUSINESS_DATE, title),
                    ).fetchone()[0]
                    connection.execute(
                        """
                        INSERT INTO stock.news_article_raw_processed_map (
                            raw_article_id, processed_article_id
                        )
                        VALUES (%s, %s)
                        """,
                        (raw_id, processed_id),
                    )

            cluster_specs = (
                (
                    'ready',
                    1,
                    'Acme revenue event',
                    'ready-a',
                    ('ready-a', 'ready-b', 'ready-negative'),
                ),
                ('singleton', 2, 'Singleton event', 'singleton', ('singleton',)),
                (
                    'failure',
                    3,
                    'Forced provider failure',
                    'failure-a',
                    ('failure-a', 'failure-b'),
                ),
            )
            cluster_ids: dict[str, int] = {}
            cluster_uids: dict[str, str] = {}
            for name, rank, title, representative, members in cluster_specs:
                cluster_id, cluster_uid = connection.execute(
                    """
                    INSERT INTO stock.news_cluster (
                        business_date, market_type, cluster_rank, title,
                        summary_short, representative_article_id, article_count
                    )
                    VALUES (%s, 'US', %s, %s, %s, %s, %s)
                    RETURNING id, cluster_uid
                    """,
                    (
                        BUSINESS_DATE,
                        rank,
                        title,
                        title,
                        article_ids[representative],
                        len(members),
                    ),
                ).fetchone()
                cluster_ids[name] = cluster_id
                cluster_uids[name] = str(cluster_uid)
                for article_rank, member in enumerate(members, start=1):
                    connection.execute(
                        """
                        INSERT INTO stock.news_cluster_article (
                            cluster_id, processed_article_id, article_rank
                        )
                        VALUES (%s, %s, %s)
                        """,
                        (cluster_id, article_ids[member], article_rank),
                    )

    return {
        'job_id': job_id,
        'page_id': page_id,
        'article_ids': article_ids,
        'cluster_ids': cluster_ids,
        'cluster_uids': cluster_uids,
    }


async def _run_production_grouping(
    dsn: str,
    fixture: dict[str, object],
) -> dict[str, object]:
    async_dsn = dsn.replace('postgresql://', 'postgresql+psycopg://', 1)
    engine = create_async_engine(async_dsn)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    requests: list[dict[str, object]] = []

    def transport(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        inputs = payload['input']
        if any('forced provider failure' in value for value in inputs):
            return httpx.Response(503, request=request)
        vectors = [
            [0.0, 1.0] if 'singleton event' in value else [1.0, 0.0] for value in inputs
        ]
        return httpx.Response(200, json={'embeddings': vectors}, request=request)

    settings = SimpleNamespace(
        ollama_base_url='http://mock-ollama.invalid',
        ollama_embed_model='bge-m3',
        ollama_timeout_seconds=1.0,
        ollama_max_retries=0,
        similarity_input_chars=2048,
        similarity_threshold=0.45,
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(transport)
        ) as client:
            provider = OllamaEmbeddingProvider(settings, client=client)
            step = GroupSimilarArticlesStep(
                embedding_provider_factory=lambda: provider,
                cluster_repo_factory=ClusterRepository,
                group_repo_factory=ArticleGroupRepository,
                settings=settings,
            )
            async with sessions() as session:
                context = await step.run(
                    BatchJobRepository(session),
                    BatchExecutionContext(
                        job_id=int(fixture['job_id']),
                        business_date=BUSINESS_DATE,
                        force_run=False,
                        rebuild_page_only=False,
                    ),
                )
                await session.commit()

        async with sessions() as session:
            grouping_repo = ArticleGroupRepository(session)
            groupings = {
                name: await grouping_repo.get_cluster_grouping(cluster_id)
                for name, cluster_id in dict(fixture['cluster_ids']).items()
            }
            page_status = await session.scalar(
                text(
                    """
                    SELECT status::text
                    FROM stock.market_daily_page
                    WHERE id = :page_id
                    """
                ),
                {'page_id': fixture['page_id']},
            )
            group_counts = {
                name: await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM stock.news_cluster_similar_group
                        WHERE cluster_id = :cluster_id
                        """
                    ),
                    {'cluster_id': cluster_id},
                )
                for name, cluster_id in dict(fixture['cluster_ids']).items()
            }
            vector_extension_count = await session.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM pg_extension
                    WHERE extname = 'vector'
                    """
                )
            )
            vector_column_count = await session.scalar(
                text(
                    """
                    SELECT count(*)
                    FROM information_schema.columns
                    WHERE table_schema = 'stock'
                      AND (
                          lower(column_name) LIKE '%vector%'
                          OR lower(column_name) LIKE '%embedding%'
                          OR lower(udt_name) = 'vector'
                      )
                    """
                )
            )
            restart_groupings = {
                name: await grouping_repo.get_cluster_grouping(cluster_id)
                for name, cluster_id in dict(fixture['cluster_ids']).items()
            }
    finally:
        await engine.dispose()

    return {
        'context': context,
        'requests': requests,
        'groupings': groupings,
        'restart_groupings': restart_groupings,
        'page_status': page_status,
        'group_counts': group_counts,
        'vector_extension_count': vector_extension_count,
        'vector_column_count': vector_column_count,
    }


def test_production_grouping_persists_with_postgres_and_is_cluster_local(
    postgres_dsn,
):
    fixture = _seed_fixture(postgres_dsn)
    before_page_status = 'READY'
    result = asyncio.run(_run_production_grouping(postgres_dsn, fixture))

    assert [len(payload['input']) for payload in result['requests']] == [3, 1, 2]
    groupings = result['groupings']
    assert groupings['ready'].status == 'READY'
    assert [
        [member.processed_article_id for member in group.members]
        for group in groupings['ready'].groups
    ] == [
        [fixture['article_ids']['ready-a'], fixture['article_ids']['ready-b']],
        [fixture['article_ids']['ready-negative']],
    ]
    assert [member.exact_duplicate_count for member in groupings['ready'].members] == [
        2,
        0,
        0,
    ]
    assert groupings['singleton'].status == 'READY'
    assert [
        member.exact_duplicate_count for member in groupings['singleton'].members
    ] == [4]
    assert groupings['failure'].status == 'UNAVAILABLE'
    assert groupings['failure'].issue_code == 'SIMILARITY_GROUPING_FAILED'
    assert [member.processed_article_id for member in groupings['failure'].members] == [
        fixture['article_ids']['failure-a'],
        fixture['article_ids']['failure-b'],
    ]
    assert [
        member.exact_duplicate_count for member in groupings['failure'].members
    ] == [
        1,
        0,
    ]
    assert groupings['ready'].status != 'UNAVAILABLE'
    assert groupings['singleton'].status != 'UNAVAILABLE'
    assert result['context'].partial_reasons == []
    assert result['page_status'] == before_page_status
    assert result['group_counts'] == {'ready': 2, 'singleton': 1, 'failure': 2}
    assert result['restart_groupings'] == result['groupings']
    assert result['vector_extension_count'] == 0
    assert result['vector_column_count'] == 0
