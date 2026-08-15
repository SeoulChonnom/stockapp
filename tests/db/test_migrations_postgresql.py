from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, pool, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.batch.ai_retry.resolver import resolve_effective_summaries
from app.batch.article_similarity import (
    SimilarityGroup,
    SimilarityGroupingResult,
    SimilarityGroupMember,
)
from app.batch.theme_rules import CANONICAL_LEAF_CODES, load_theme_rules
from app.core.settings import Settings
from app.core.text import normalize_search_document
from app.db import migration_runner
from app.db.repositories.ai_retry_repo import PostgresAiRetryRepository
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.article_group_repo import ArticleGroupRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.news_cluster_write_repo import NewsClusterWriteRepository
from app.db.repositories.projections import (
    BatchJobMarketContextCreateParams,
    ExactDuplicateCountRecord,
    NewsClusterArticleCreateParams,
)

psycopg = pytest.importorskip('psycopg')

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPOSITORY_ROOT / 'db' / 'schema_postgresql.sql'
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / 'db' / 'migrations'
DATE_DEDUPE_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260728_03_news_article_processed_date_dedupe.sql'
)
MARKET_SESSION_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260729_05_market_session_context_source_date.sql'
)
INCREMENTAL_NEWS_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260731_07_incremental_news_collection.sql'
)
THEME_MIGRATION = MIGRATIONS_DIRECTORY / '20260813_08_theme_catalog_archive_search.sql'
PAGE_SEARCH_MIGRATION = MIGRATIONS_DIRECTORY / '20260814_09_page_search_document.sql'
SIMILARITY_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260813_09_article_similarity_groups.sql'
)
GROUP_RANK_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260815_10_article_link_group_rank_not_null.sql'
)
ALEMBIC_BASELINE_SQL = (
    REPOSITORY_ROOT / 'db' / 'alembic' / 'baselines' / '20260731_schema.sql'
)
ALEMBIC_PREVIOUS_HEAD = '20260810_01_step_errors'
ALEMBIC_THEME_REVISION = '20260814_01_theme_archive_search'
ALEMBIC_PAGE_SEARCH_REVISION = '20260814_02_page_search_document'
ALEMBIC_SIMILARITY_REVISION = '20260814_03_article_similarity_groups'
ALEMBIC_HEAD = '20260815_01_group_rank_not_null'


def _execute_file(connection, path: Path) -> None:
    connection.execute(path.read_text(encoding='utf-8'))


def _run_alembic(
    engine,
    database_url: str,
    action,
    target: str,
) -> None:
    config = Config(str(REPOSITORY_ROOT / 'alembic.ini'))
    config.set_main_option(
        'sqlalchemy.url',
        database_url.replace('postgresql://', 'postgresql+psycopg://', 1),
    )
    with engine.connect() as connection:
        connection.execute(text('SET search_path TO "$user", public'))
        connection.commit()
        config.attributes['connection'] = connection
        config.attributes['version_table_schema'] = 'stock'
        action(config, target)


def _execute_all_migrations_twice(connection) -> None:
    migration_files = sorted(MIGRATIONS_DIRECTORY.glob('*.sql'))
    for _ in range(2):
        for migration_file in migration_files:
            _execute_file(connection, migration_file)


@pytest.fixture
def postgres_connection():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS stock CASCADE')
        _execute_file(connection, SCHEMA_SQL)
        try:
            yield connection
        finally:
            connection.rollback()
            connection.execute('DROP SCHEMA IF EXISTS stock CASCADE')


def test_migration_files_execute_whole_and_are_idempotent(postgres_connection):
    _execute_all_migrations_twice(postgres_connection)

    constraints = postgres_connection.execute(
        """
        SELECT pg_get_constraintdef(oid)
        FROM pg_constraint
        WHERE conrelid = 'stock.news_article_processed'::regclass
          AND conname =
              'uq_news_article_processed_business_date_dedupe_hash'
        """
    ).fetchall()
    keywords = postgres_connection.execute(
        """
        SELECT market_type::text, keyword, is_active
        FROM stock.news_search_keyword
        WHERE provider_name = 'NAVER_NEWS'
        ORDER BY market_type
        """
    ).fetchall()

    assert constraints == [('UNIQUE (business_date, market_type, dedupe_hash)',)]
    assert keywords == [
        ('KR', '코스피', True),
        ('US', '미국 증시', True),
    ]


def test_incremental_news_migration_transfers_duplicate_keyword_relationships(
    postgres_connection,
):
    postgres_connection.execute(
        """
        ALTER TABLE stock.news_article_raw
            DROP CONSTRAINT uq_news_article_raw_provider_key;
        ALTER TABLE stock.news_article_raw
            ADD CONSTRAINT uq_news_article_raw_business_provider_key
            UNIQUE (business_date, provider_name, provider_article_key);

        INSERT INTO stock.news_article_raw (
            provider_name,
            provider_article_key,
            market_type,
            business_date,
            search_keyword,
            title,
            published_at,
            origin_link
        )
        VALUES
            (
                'NAVER_NEWS',
                'shared-key',
                'KR',
                DATE '2026-07-30',
                '코스피',
                'Shared article',
                TIMESTAMPTZ '2026-07-30 01:00:00+00',
                'https://example.com/shared'
            ),
            (
                'NAVER_NEWS',
                'shared-key',
                'US',
                DATE '2026-07-31',
                '미국 증시',
                'Shared article',
                TIMESTAMPTZ '2026-07-30 01:00:00+00',
                'https://example.com/shared'
            );
        """
    )

    _execute_file(postgres_connection, INCREMENTAL_NEWS_MIGRATION)
    _execute_file(postgres_connection, INCREMENTAL_NEWS_MIGRATION)

    raw_rows = postgres_connection.execute(
        """
        SELECT id, provider_article_key
        FROM stock.news_article_raw
        WHERE provider_name = 'NAVER_NEWS'
          AND provider_article_key = 'shared-key'
        """
    ).fetchall()
    relationships = postgres_connection.execute(
        """
        SELECT keyword.market_type::text, keyword.keyword
        FROM stock.news_article_raw_keyword_match keyword_match
        JOIN stock.news_search_keyword keyword
          ON keyword.id = keyword_match.keyword_id
        JOIN stock.news_article_raw raw
          ON raw.id = keyword_match.raw_article_id
        WHERE raw.provider_name = 'NAVER_NEWS'
          AND raw.provider_article_key = 'shared-key'
        ORDER BY keyword.market_type
        """
    ).fetchall()

    assert len(raw_rows) == 1
    assert relationships == [
        ('US', '미국 증시'),
        ('KR', '코스피'),
    ]


def test_durable_queue_claims_do_not_block_competing_workers(
    postgres_connection,
):
    worker_dsn = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode
        )
        VALUES
            (DATE '2026-08-01', 'PENDING', 'MANUAL', 'FULL'),
            (DATE '2026-08-02', 'PENDING', 'MANUAL', 'FULL')
        """
    )

    with (
        psycopg.connect(worker_dsn) as first_worker,
        psycopg.connect(worker_dsn) as second_worker,
    ):
        first_job_id = first_worker.execute(
            """
            SELECT id
            FROM stock.batch_job
            WHERE status = 'PENDING'
            ORDER BY available_at, queued_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()[0]
        second_job_id = second_worker.execute(
            """
            SELECT id
            FROM stock.batch_job
            WHERE status = 'PENDING'
            ORDER BY available_at, queued_at, id
            FOR UPDATE SKIP LOCKED
            LIMIT 1
            """
        ).fetchone()[0]

        assert second_job_id != first_job_id
        first_worker.rollback()
        second_worker.rollback()


@pytest.mark.anyio
async def test_durable_queue_repository_recovers_and_fences_expired_lease(
    postgres_connection,
):
    postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode
        )
        VALUES (DATE '2026-08-03', 'PENDING', 'MANUAL', 'FULL')
        """
    )
    database_url = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    async_database_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_async_engine(async_database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    first_lease_token = uuid4()
    second_lease_token = uuid4()

    try:
        async with session_maker() as session:
            repository = BatchJobRepository(session)
            first_claim = await repository.claim_next_job(
                worker_id='worker-a',
                lease_token=first_lease_token,
                lease_seconds=120,
            )
            assert first_claim is not None
            assert first_claim.attempt_count == 1
            await repository.commit()

            stale_heartbeat = await repository.heartbeat_claim(
                job_id=first_claim.job_id,
                worker_id='worker-a',
                lease_token=uuid4(),
                lease_seconds=120,
            )
            valid_heartbeat = await repository.heartbeat_claim(
                job_id=first_claim.job_id,
                worker_id='worker-a',
                lease_token=first_lease_token,
                lease_seconds=120,
            )
            assert stale_heartbeat is False
            assert valid_heartbeat is True
            await repository.commit()

            await session.execute(
                text(
                    """
                    UPDATE stock.batch_job
                    SET lease_expires_at = now() - interval '1 second'
                    WHERE id = :job_id
                    """
                ),
                {'job_id': first_claim.job_id},
            )
            await repository.commit()

            recovery = await repository.recover_expired_claims()
            assert recovery.requeued_count == 1
            assert recovery.failed_count == 0
            await repository.commit()

            second_claim = await repository.claim_next_job(
                worker_id='worker-b',
                lease_token=second_lease_token,
                lease_seconds=120,
            )
            assert second_claim is not None
            assert second_claim.job_id == first_claim.job_id
            assert second_claim.attempt_count == 2
            await repository.commit()

            old_lease_heartbeat = await repository.heartbeat_claim(
                job_id=second_claim.job_id,
                worker_id='worker-a',
                lease_token=first_lease_token,
                lease_seconds=120,
            )
            new_lease_heartbeat = await repository.heartbeat_claim(
                job_id=second_claim.job_id,
                worker_id='worker-b',
                lease_token=second_lease_token,
                lease_seconds=120,
            )
            assert old_lease_heartbeat is False
            assert new_lease_heartbeat is True
            await repository.commit()
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_real_repositories_preserve_context_and_multi_hop_ai_lineage(
    postgres_connection,
):
    root_job_id = postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode
        )
        VALUES (DATE '2026-08-04', 'PARTIAL', 'MANUAL', 'FULL')
        RETURNING id
        """
    ).fetchone()[0]
    root_page_id = postgres_connection.execute(
        """
        INSERT INTO stock.market_daily_page (
            business_date,
            version_no,
            page_title,
            status,
            batch_job_id
        )
        VALUES (
            DATE '2026-08-04',
            1,
            'root page',
            'PARTIAL',
            %s
        )
        RETURNING id
        """,
        (root_job_id,),
    ).fetchone()[0]
    first_retry_job_id = postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode,
            source_job_id,
            source_page_id
        )
        VALUES (
            DATE '2026-08-04',
            'PARTIAL',
            'ADMIN_REBUILD',
            'AI_RETRY',
            %s,
            %s
        )
        RETURNING id
        """,
        (root_job_id, root_page_id),
    ).fetchone()[0]
    first_retry_page_id = postgres_connection.execute(
        """
        INSERT INTO stock.market_daily_page (
            business_date,
            version_no,
            page_title,
            status,
            batch_job_id
        )
        VALUES (
            DATE '2026-08-04',
            2,
            'first retry page',
            'PARTIAL',
            %s
        )
        RETURNING id
        """,
        (first_retry_job_id,),
    ).fetchone()[0]
    rebuild_job_id = postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode,
            source_job_id,
            source_page_id
        )
        VALUES (
            DATE '2026-08-04',
            'SUCCESS',
            'ADMIN_REBUILD',
            'PAGE_REBUILD',
            %s,
            %s
        )
        RETURNING id
        """,
        (first_retry_job_id, first_retry_page_id),
    ).fetchone()[0]
    unrelated_full_job_id = postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            run_mode,
            source_job_id
        )
        VALUES (
            DATE '2026-08-04',
            'SUCCESS',
            'MANUAL',
            'FULL',
            %s
        )
        RETURNING id
        """,
        (root_job_id,),
    ).fetchone()[0]

    root_summary_id = postgres_connection.execute(
        """
        INSERT INTO stock.ai_summary (
            batch_job_id,
            summary_type,
            business_date,
            title,
            body,
            status,
            fallback_used,
            target_key,
            attempt_no
        )
        VALUES (
            %s,
            'GLOBAL_HEADLINE',
            DATE '2026-08-04',
            'fallback',
            'provider fallback',
            'FALLBACK',
            TRUE,
            'GLOBAL_HEADLINE',
            1
        )
        RETURNING id
        """,
        (root_job_id,),
    ).fetchone()[0]
    first_retry_summary_id = postgres_connection.execute(
        """
        INSERT INTO stock.ai_summary (
            batch_job_id,
            summary_type,
            business_date,
            title,
            body,
            status,
            fallback_used,
            target_key,
            source_summary_id,
            attempt_no
        )
        VALUES (
            %s,
            'GLOBAL_HEADLINE',
            DATE '2026-08-04',
            'retry failed',
            'retry failed',
            'FAILED',
            FALSE,
            'GLOBAL_HEADLINE',
            %s,
            2
        )
        RETURNING id
        """,
        (first_retry_job_id, root_summary_id),
    ).fetchone()[0]
    postgres_connection.execute(
        """
        INSERT INTO stock.ai_summary (
            batch_job_id,
            summary_type,
            business_date,
            title,
            body,
            status,
            fallback_used,
            target_key,
            source_summary_id,
            attempt_no
        )
        VALUES (
            %s,
            'GLOBAL_HEADLINE',
            DATE '2026-08-04',
            'retry recovered',
            'usable provider summary',
            'SUCCESS',
            FALSE,
            'GLOBAL_HEADLINE',
            %s,
            3
        )
        """,
        (rebuild_job_id, first_retry_summary_id),
    )
    postgres_connection.execute(
        """
        INSERT INTO stock.ai_summary (
            batch_job_id,
            summary_type,
            business_date,
            title,
            body,
            status,
            fallback_used,
            target_key,
            attempt_no
        )
        VALUES (
            %s,
            'GLOBAL_HEADLINE',
            DATE '2026-08-04',
            'unrelated full',
            'must not enter retry lineage',
            'SUCCESS',
            FALSE,
            'GLOBAL_HEADLINE',
            99
        )
        """,
        (unrelated_full_job_id,),
    )

    database_url = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    async_database_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_async_engine(async_database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            context_repository = MarketContextRepository(session)
            context_params = BatchJobMarketContextCreateParams(
                batch_job_id=root_job_id,
                market_type='US',
                expected_session_date=date(2026, 8, 3),
                session_close_at=datetime(2026, 8, 3, 20, tzinfo=UTC),
                news_window_start_at=datetime(2026, 8, 2, 20, tzinfo=UTC),
                news_window_end_at=datetime(2026, 8, 3, 20, tzinfo=UTC),
            )
            await context_repository.insert_if_absent(context_params)
            await context_repository.insert_if_absent(context_params)
            await context_repository.set_actual_index_source_date(
                job_id=root_job_id,
                market_type='US',
                source_date=date(2026, 8, 3),
            )
            await context_repository.set_news_coverage_complete(
                job_id=root_job_id,
                market_type='US',
                coverage_complete=True,
            )
            await session.commit()

            contexts = await context_repository.list_for_job(root_job_id)
            assert len(contexts) == 1
            assert contexts[0].actual_index_source_date == date(2026, 8, 3)
            assert contexts[0].news_coverage_complete is True

            retry_repository = PostgresAiRetryRepository(session, max_attempts=5)
            source = await retry_repository.resolve_source(rebuild_job_id)
            assert source is not None
            assert source.source_job_id == root_job_id
            assert source.source_page_id == first_retry_page_id
            assert source.source_status == 'SUCCESS'
            enqueued = await retry_repository.enqueue(
                source=source,
                triggered_by_user_id='ADMIN-1',
                idempotency_key='real-lineage-retry',
            )
            assert enqueued.created is True
            assert enqueued.job.status == 'PENDING'
            await retry_repository.commit()
            retry_max_attempts = await session.scalar(
                text(
                    """
                    SELECT max_attempts
                    FROM stock.batch_job
                    WHERE id = :job_id
                    """
                ),
                {'job_id': enqueued.job.job_id},
            )
            assert retry_max_attempts == 5

            lineage = await AiSummaryRepository(session).list_retry_lineage_summaries(
                root_job_id
            )
            assert {row.batch_job_id for row in lineage} == {
                root_job_id,
                first_retry_job_id,
                rebuild_job_id,
            }
            effective = resolve_effective_summaries(lineage)
            assert effective['GLOBAL_HEADLINE'].body == 'usable provider summary'
            assert effective['GLOBAL_HEADLINE'].attempt_no == 3
    finally:
        await engine.dispose()


def test_market_session_migration_upgrades_legacy_contract_idempotently(
    postgres_connection,
):
    postgres_connection.execute(
        """
        DROP TABLE stock.batch_job_market_context;

        ALTER TABLE stock.market_index_daily
            DROP CONSTRAINT chk_market_index_daily_source_not_future,
            DROP COLUMN source_date,
            DROP COLUMN expected_session_date,
            DROP COLUMN session_close_at;

        ALTER TABLE stock.market_daily_page_market
            DROP CONSTRAINT chk_market_daily_page_market_news_window,
            DROP CONSTRAINT chk_market_daily_page_market_source_not_future,
            DROP COLUMN expected_session_date,
            DROP COLUMN actual_index_source_date,
            DROP COLUMN session_close_at,
            DROP COLUMN news_window_start_at,
            DROP COLUMN news_window_end_at,
            DROP COLUMN news_coverage_complete;

        ALTER TABLE stock.market_daily_page_market_index
            DROP CONSTRAINT
                chk_market_daily_page_market_index_source_not_future,
            DROP COLUMN source_date,
            DROP COLUMN expected_session_date,
            DROP COLUMN session_close_at;

        ALTER TABLE stock.news_article_raw
            DROP CONSTRAINT uq_news_article_raw_provider_key,
            ADD CONSTRAINT uq_news_article_raw_business_provider_key
                UNIQUE (business_date, provider_name, provider_article_key);

        INSERT INTO stock.market_index_daily (
            business_date,
            market_type,
            index_code,
            index_name,
            close_price,
            change_value,
            change_percent,
            currency_code,
            provider_name
        )
        VALUES (
            DATE '2026-07-24',
            'US',
            'LEGACY_INDEX',
            'Legacy index row',
            100,
            1,
            1,
            'USD',
            'LEGACY'
        );
        """
    )

    _execute_file(postgres_connection, MARKET_SESSION_MIGRATION)
    _execute_file(postgres_connection, MARKET_SESSION_MIGRATION)

    context_columns = postgres_connection.execute(
        """
        SELECT column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'stock'
          AND table_name = 'batch_job_market_context'
          AND column_name IN (
              'expected_session_date',
              'session_close_at',
              'news_window_start_at',
              'news_window_end_at',
              'news_coverage_complete'
          )
        ORDER BY column_name
        """
    ).fetchall()
    index_columns = postgres_connection.execute(
        """
        SELECT column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'stock'
          AND table_name = 'market_index_daily'
          AND column_name IN (
              'source_date',
              'expected_session_date',
              'session_close_at'
          )
        ORDER BY column_name
        """
    ).fetchall()
    required_value_constraints = postgres_connection.execute(
        """
        SELECT conname, convalidated
        FROM pg_constraint
        WHERE conrelid = 'stock.market_index_daily'::regclass
          AND conname IN (
              'chk_market_index_daily_source_date_present',
              'chk_market_index_daily_expected_session_date_present',
              'chk_market_index_daily_session_close_at_present'
          )
        ORDER BY conname
        """
    ).fetchall()
    legacy_market_dates = postgres_connection.execute(
        """
        SELECT source_date, expected_session_date, session_close_at
        FROM stock.market_index_daily
        WHERE index_code = 'LEGACY_INDEX'
        """
    ).fetchone()

    with pytest.raises(psycopg.errors.CheckViolation):
        postgres_connection.execute(
            """
            INSERT INTO stock.market_index_daily (
                business_date,
                market_type,
                source_date,
                expected_session_date,
                session_close_at,
                index_code,
                index_name,
                close_price,
                change_value,
                change_percent,
                currency_code,
                provider_name
            )
            VALUES (
                DATE '2026-07-25',
                'US',
                NULL,
                DATE '2026-07-24',
                TIMESTAMPTZ '2026-07-24 20:00:00+00',
                'INVALID_NEW_INDEX',
                'Invalid new index row',
                100,
                1,
                1,
                'USD',
                'TEST'
            )
            """
        )

    postgres_connection.execute(
        """
        INSERT INTO stock.news_article_raw (
            provider_name,
            provider_article_key,
            market_type,
            business_date,
            title
        )
        VALUES
            (
                'NAVER_NEWS',
                'same-provider-key',
                'US',
                DATE '2026-07-28',
                'First publication date'
            ),
            (
                'NAVER_NEWS',
                'same-provider-key',
                'US',
                DATE '2026-07-29',
                'Second publication date'
            );
        """
    )
    repeated_provider_key_count = postgres_connection.execute(
        """
        SELECT count(*)
        FROM stock.news_article_raw
        WHERE provider_name = 'NAVER_NEWS'
          AND provider_article_key = 'same-provider-key'
        """
    ).fetchone()

    assert context_columns == [
        ('expected_session_date', 'NO'),
        ('news_coverage_complete', 'NO'),
        ('news_window_end_at', 'NO'),
        ('news_window_start_at', 'NO'),
        ('session_close_at', 'NO'),
    ]
    assert index_columns == [
        ('expected_session_date', 'YES'),
        ('session_close_at', 'YES'),
        ('source_date', 'YES'),
    ]
    assert required_value_constraints == [
        ('chk_market_index_daily_expected_session_date_present', False),
        ('chk_market_index_daily_session_close_at_present', False),
        ('chk_market_index_daily_source_date_present', False),
    ]
    assert legacy_market_dates == (None, None, None)
    assert repeated_provider_key_count == (2,)


def test_migration_files_upgrade_legacy_contracts_as_whole_files(
    postgres_connection,
):
    postgres_connection.execute(
        """
        ALTER TABLE stock.batch_job
            ALTER COLUMN triggered_by_user_id TYPE UUID
            USING triggered_by_user_id::uuid;
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            triggered_by_user_id
        )
        VALUES (
            DATE '2026-07-27',
            'SUCCESS',
            'MANUAL',
            '10d91197-59f6-4d2f-8177-c90335b9f53d'
        );

        UPDATE stock.news_search_keyword
        SET is_active = FALSE, priority = 50
        WHERE provider_name = 'NAVER_NEWS'
          AND market_type = 'US';
        INSERT INTO stock.news_search_keyword (
            provider_name,
            market_type,
            keyword,
            is_active,
            priority
        )
        VALUES
            ('NAVER_NEWS_SEARCH', 'US', '미국 증시', TRUE, 20),
            ('NAVER_NEWS_SEARCH', 'KR', '코스닥', TRUE, 30);

        ALTER TABLE stock.news_article_processed
            DROP CONSTRAINT
                uq_news_article_processed_business_date_dedupe_hash;
        ALTER TABLE stock.news_article_processed
            ADD CONSTRAINT uq_news_article_processed_dedupe_hash
            UNIQUE (dedupe_hash);
        """
    )

    _execute_all_migrations_twice(postgres_connection)

    postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (
            business_date,
            status,
            trigger_type,
            triggered_by_user_id
        )
        VALUES (
            DATE '2026-07-28',
            'SUCCESS',
            'MANUAL',
            'USER-0001'
        );
        INSERT INTO stock.news_article_processed (
            business_date,
            market_type,
            dedupe_hash,
            canonical_title,
            origin_link
        )
        VALUES
            (
                DATE '2026-07-27',
                'US',
                repeat('a', 64),
                'Day one',
                'https://example.com/day-1'
            ),
            (
                DATE '2026-07-28',
                'US',
                repeat('a', 64),
                'Day two',
                'https://example.com/day-2'
            );
        """
    )

    triggered_subjects = postgres_connection.execute(
        """
        SELECT triggered_by_user_id
        FROM stock.batch_job
        ORDER BY business_date
        """
    ).fetchall()
    legacy_provider_count = postgres_connection.execute(
        """
        SELECT count(*)
        FROM stock.news_search_keyword
        WHERE provider_name = 'NAVER_NEWS_SEARCH'
        """
    ).fetchone()
    processed_article_count = postgres_connection.execute(
        """
        SELECT count(*)
        FROM stock.news_article_processed
        WHERE dedupe_hash = repeat('a', 64)
        """
    ).fetchone()

    assert triggered_subjects == [
        ('10d91197-59f6-4d2f-8177-c90335b9f53d',),
        ('USER-0001',),
    ]
    assert legacy_provider_count == (0,)
    assert processed_article_count == (2,)


def test_date_dedupe_migration_recovers_invalid_named_index(
    postgres_connection,
):
    postgres_connection.execute(
        """
        ALTER TABLE stock.news_article_processed
            DROP CONSTRAINT
                uq_news_article_processed_business_date_dedupe_hash;

        INSERT INTO stock.news_article_processed (
            business_date,
            market_type,
            dedupe_hash,
            canonical_title,
            origin_link
        )
        VALUES
            (
                DATE '2026-07-28',
                'US',
                repeat('a', 64),
                'Duplicate one',
                'https://example.com/duplicate-1'
            ),
            (
                DATE '2026-07-28',
                'US',
                repeat('a', 64),
                'Duplicate two',
                'https://example.com/duplicate-2'
            );
        """
    )

    with pytest.raises(psycopg.errors.UniqueViolation):
        postgres_connection.execute(
            """
            CREATE UNIQUE INDEX CONCURRENTLY
                uq_news_article_processed_business_date_dedupe_hash
            ON stock.news_article_processed (business_date, dedupe_hash)
            """
        )

    index_validity = postgres_connection.execute(
        """
        SELECT index_state.indisvalid
        FROM pg_index AS index_state
        JOIN pg_class AS index_class
          ON index_class.oid = index_state.indexrelid
        JOIN pg_namespace AS index_namespace
          ON index_namespace.oid = index_class.relnamespace
        WHERE index_namespace.nspname = 'stock'
          AND index_class.relname =
              'uq_news_article_processed_business_date_dedupe_hash'
        """
    ).fetchone()
    assert index_validity == (False,)

    with pytest.raises(
        psycopg.errors.UniqueViolation,
        match='Duplicate processed articles prevent date-scoped dedupe',
    ):
        _execute_file(postgres_connection, DATE_DEDUPE_MIGRATION)
    postgres_connection.rollback()

    postgres_connection.execute(
        """
        DELETE FROM stock.news_article_processed
        WHERE id = (
            SELECT max(id)
            FROM stock.news_article_processed
            WHERE business_date = DATE '2026-07-28'
              AND dedupe_hash = repeat('a', 64)
        )
        """
    )

    _execute_file(postgres_connection, DATE_DEDUPE_MIGRATION)
    _execute_file(postgres_connection, DATE_DEDUPE_MIGRATION)

    recovered_index = postgres_connection.execute(
        """
        SELECT index_state.indisvalid, index_state.indisunique
        FROM pg_index AS index_state
        JOIN pg_class AS index_class
          ON index_class.oid = index_state.indexrelid
        JOIN pg_namespace AS index_namespace
          ON index_namespace.oid = index_class.relnamespace
        WHERE index_namespace.nspname = 'stock'
          AND index_class.relname =
              'uq_news_article_processed_business_date_dedupe_hash'
        """
    ).fetchone()
    assert recovered_index == (True, True)


def test_theme_catalog_has_canonical_tree_constraints_and_idempotent_seed(
    postgres_connection,
):
    tree_counts = postgres_connection.execute(
        """
        WITH RECURSIVE theme_tree AS (
            SELECT
                code,
                parent_code,
                1 AS depth,
                ARRAY[code] AS path
            FROM stock.theme_catalog
            WHERE parent_code IS NULL
            UNION ALL
            SELECT
                child.code,
                child.parent_code,
                theme_tree.depth + 1,
                theme_tree.path || child.code
            FROM theme_tree
            JOIN stock.theme_catalog AS child
              ON child.parent_code = theme_tree.code
            WHERE NOT child.code = ANY(theme_tree.path)
        )
        SELECT
            count(*) AS reachable_count,
            count(DISTINCT code) AS distinct_count,
            count(*) FILTER (WHERE depth = 1) AS root_count,
            count(*) FILTER (WHERE depth = 2) AS intermediate_count,
            count(*) FILTER (WHERE depth = 3) AS leaf_count,
            max(depth) AS max_depth
        FROM theme_tree
        """
    ).fetchone()
    assert tree_counts == (63, 63, 5, 18, 40, 3)

    invalid_code_count, self_parent_count = postgres_connection.execute(
        """
        SELECT
            count(*) FILTER (WHERE code !~ '^[A-Z0-9_]+$'),
            count(*) FILTER (WHERE code = parent_code)
        FROM stock.theme_catalog
        """
    ).fetchone()
    assert invalid_code_count == 0
    assert self_parent_count == 0

    constraint_rows = postgres_connection.execute(
        """
        SELECT namespace.nspname, relation.relname, constraint_.conname,
               constraint_.contype
        FROM pg_constraint AS constraint_
        JOIN pg_class AS relation
          ON relation.oid = constraint_.conrelid
        JOIN pg_namespace AS namespace
          ON namespace.oid = relation.relnamespace
        WHERE constraint_.conrelid IN (
            'stock.news_cluster_theme'::regclass,
            'stock.market_daily_page_market_cluster_theme'::regclass
        )
          AND constraint_.conname IN (
              'chk_news_cluster_theme_rank',
              'chk_market_daily_page_market_cluster_theme_rank',
              'news_cluster_theme_pkey',
              'market_daily_page_market_cluster_theme_pkey',
              'uq_news_cluster_theme_cluster_rank',
              'uq_market_daily_page_market_cluster_theme_cluster_rank'
          )
        ORDER BY namespace.nspname, relation.relname, constraint_.conname
        """
    ).fetchall()
    assert {
        (schema, table, name, kind) for schema, table, name, kind in constraint_rows
    } == {
        (
            'stock',
            'news_cluster_theme',
            'chk_news_cluster_theme_rank',
            'c',
        ),
        (
            'stock',
            'news_cluster_theme',
            'news_cluster_theme_pkey',
            'p',
        ),
        (
            'stock',
            'news_cluster_theme',
            'uq_news_cluster_theme_cluster_rank',
            'u',
        ),
        (
            'stock',
            'market_daily_page_market_cluster_theme',
            'chk_market_daily_page_market_cluster_theme_rank',
            'c',
        ),
        (
            'stock',
            'market_daily_page_market_cluster_theme',
            'market_daily_page_market_cluster_theme_pkey',
            'p',
        ),
        (
            'stock',
            'market_daily_page_market_cluster_theme',
            'uq_market_daily_page_market_cluster_theme_cluster_rank',
            'u',
        ),
    }

    before_seed = postgres_connection.execute(
        """
        SELECT code, parent_code, label, description, sort_order, is_active
        FROM stock.theme_catalog
        ORDER BY code
        """
    ).fetchall()
    _execute_file(postgres_connection, THEME_MIGRATION)
    after_seed = postgres_connection.execute(
        """
        SELECT code, parent_code, label, description, sort_order, is_active
        FROM stock.theme_catalog
        ORDER BY code
        """
    ).fetchall()
    assert after_seed == before_seed

    search_columns = postgres_connection.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = 'stock'
          AND column_name = 'search_document'
          AND table_name IN (
              'market_daily_page',
              'market_daily_page_market',
              'market_daily_page_market_cluster'
          )
        ORDER BY table_name
        """
    ).fetchall()
    assert search_columns == [
        ('market_daily_page', 'search_document'),
        ('market_daily_page_market', 'search_document'),
        ('market_daily_page_market_cluster', 'search_document'),
    ]


def test_page_search_document_migration_backfills_unicode_normalization(
    postgres_connection,
):
    job_id = postgres_connection.execute(
        """
        INSERT INTO stock.batch_job (business_date, status)
        VALUES (DATE '2026-08-14', 'SUCCESS')
        RETURNING id
        """
    ).fetchone()[0]
    postgres_connection.execute(
        """
        INSERT INTO stock.market_daily_page (
            business_date,
            version_no,
            page_title,
            status,
            global_headline,
            batch_job_id
        )
        VALUES
            (
                DATE '2026-08-14',
                1,
                'Straße',
                'READY',
                NULL,
                %s
            ),
            (
                DATE '2026-08-15',
                1,
                'Café',
                'READY',
                'CAFÉ',
                %s
            ),
            (
                DATE '2026-08-16',
                1,
                'İstanbul ﬁ',
                'READY',
                NULL,
                %s
            )
        """,
        (job_id, job_id, job_id),
    )

    _execute_file(postgres_connection, PAGE_SEARCH_MIGRATION)
    _execute_file(postgres_connection, PAGE_SEARCH_MIGRATION)

    rows = postgres_connection.execute(
        """
        SELECT business_date, search_document
        FROM stock.market_daily_page
        WHERE business_date BETWEEN DATE '2026-08-14' AND DATE '2026-08-16'
        ORDER BY business_date
        """
    ).fetchall()
    assert rows == [
        (date(2026, 8, 14), 'strasse'),
        (date(2026, 8, 15), 'café café'),
        (date(2026, 8, 16), 'i̇stanbul fi'),
    ]


def test_alembic_head_applies_page_search_and_supports_snapshot_write():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, ALEMBIC_THEME_REVISION)
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text('SELECT version_num FROM stock.alembic_version')
                ).scalar_one()
                == ALEMBIC_THEME_REVISION
            )

        _run_alembic(engine, database_url, command.upgrade, 'head')
        _run_alembic(engine, database_url, command.upgrade, 'head')

        with engine.connect() as connection:
            column = connection.execute(
                text(
                    """
                    SELECT column_name, is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'stock'
                      AND table_name = 'market_daily_page'
                      AND column_name = 'search_document'
                    """
                )
            ).one()
            assert column[0] == 'search_document'
            assert column[1] == 'NO'
            assert "''::text" in column[2]

            job_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.batch_job (business_date, status)
                    VALUES (DATE '2026-08-17', 'SUCCESS')
                    RETURNING id
                    """
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page (
                        business_date,
                        version_no,
                        page_title,
                        status,
                        global_headline,
                        search_document,
                        batch_job_id
                    )
                    VALUES (
                        DATE '2026-08-17',
                        1,
                        :page_title,
                        'READY',
                        :global_headline,
                        :search_document,
                        :batch_job_id
                    )
                    """
                ),
                {
                    'page_title': 'Straße',
                    'global_headline': 'CAFÉ',
                    'search_document': normalize_search_document('Straße', 'CAFÉ'),
                    'batch_job_id': job_id,
                },
            )
            connection.commit()

            stored_document = connection.execute(
                text(
                    """
                    SELECT search_document
                    FROM stock.market_daily_page
                    WHERE business_date = DATE '2026-08-17'
                    """
                )
            ).scalar_one()
            assert stored_document == 'strasse café'
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_alembic_page_search_backfill_matches_python_whitespace_contract():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    whitespace = ''.join(
        chr(codepoint)
        for codepoint in (
            0x0009,
            0x000A,
            0x000B,
            0x000C,
            0x000D,
            0x001C,
            0x001D,
            0x001E,
            0x001F,
            0x0020,
            0x0085,
            0x00A0,
            0x1680,
            0x2000,
            0x2001,
            0x2002,
            0x2003,
            0x2004,
            0x2005,
            0x2006,
            0x2007,
            0x2008,
            0x2009,
            0x200A,
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        )
    )
    page_title = whitespace.join(('Straße', 'Café'))
    global_headline = whitespace.join(('STRASSE', 'ﬁ'))
    expected_document = normalize_search_document(page_title, global_headline)

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, ALEMBIC_PREVIOUS_HEAD)
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    ALTER TABLE stock.market_daily_page
                        ADD COLUMN search_document TEXT
                    """
                )
            )
            job_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.batch_job (business_date, status)
                    VALUES (DATE '2026-08-18', 'SUCCESS')
                    RETURNING id
                    """
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page (
                        business_date,
                        version_no,
                        page_title,
                        status,
                        global_headline,
                        batch_job_id
                    )
                    VALUES (
                        DATE '2026-08-18',
                        1,
                        :page_title,
                        'READY',
                        :global_headline,
                        :batch_job_id
                    )
                    """
                ),
                {
                    'page_title': page_title,
                    'global_headline': global_headline,
                    'batch_job_id': job_id,
                },
            )
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, ALEMBIC_HEAD)
        _run_alembic(engine, database_url, command.upgrade, ALEMBIC_HEAD)

        with engine.connect() as connection:
            stored_document = connection.execute(
                text(
                    """
                    SELECT search_document
                    FROM stock.market_daily_page
                    WHERE business_date = DATE '2026-08-18'
                    """
                )
            ).scalar_one()
            assert stored_document == expected_document
            column = connection.execute(
                text(
                    """
                    SELECT is_nullable, column_default
                    FROM information_schema.columns
                    WHERE table_schema = 'stock'
                      AND table_name = 'market_daily_page'
                      AND column_name = 'search_document'
                    """
                )
            ).one()
            assert column[0] == 'NO'
            assert "''::text" in column[1]
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_startup_migration_adopts_unversioned_baseline_and_applies_page_revision():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    settings = Settings(
        _env_file=None,
        app_env='test',
        database_url=sqlalchemy_url,
        database_schema='stock',
        database_migration_enabled=True,
        database_migration_lock_timeout_seconds=5,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
            baseline_sql = ALEMBIC_BASELINE_SQL.read_text(encoding='utf-8').strip()
            connection.exec_driver_sql(
                baseline_sql.removeprefix('BEGIN;').removesuffix('COMMIT;').strip()
            )
            connection.commit()
            job_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.batch_job (business_date, status)
                    VALUES (DATE '2026-08-19', 'SUCCESS')
                    RETURNING id
                    """
                )
            ).scalar_one()
            connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page (
                        business_date,
                        version_no,
                        page_title,
                        status,
                        global_headline,
                        batch_job_id
                    )
                    VALUES (
                        DATE '2026-08-19',
                        1,
                        'Café',
                        'READY',
                        'STRASSE',
                        :batch_job_id
                    )
                    """
                ),
                {'batch_job_id': job_id},
            )
            connection.commit()

        migration_runner.run_startup_migrations(settings, engine=engine)
        migration_runner.run_startup_migrations(settings, engine=engine)

        with engine.connect() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT page.search_document, version.version_num
                    FROM stock.market_daily_page AS page
                    CROSS JOIN stock.alembic_version AS version
                    WHERE page.business_date = DATE '2026-08-19'
                    """
                )
            ).one()
            assert row == ('café strasse', ALEMBIC_HEAD)
            assert (
                connection.execute(
                    text('SELECT count(*) FROM stock.theme_catalog')
                ).scalar_one()
                == 63
            )
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_alembic_head_applies_theme_catalog_search_assets_and_seed_contract():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, 'head')
        _run_alembic(engine, database_url, command.upgrade, 'head')

        with engine.connect() as connection:
            tables = set(
                connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'stock'
                          AND table_name IN (
                              'theme_catalog',
                              'news_cluster_theme',
                              'market_daily_page_market_cluster_theme'
                          )
                        """
                    )
                ).scalars()
            )
            assert tables == {
                'theme_catalog',
                'news_cluster_theme',
                'market_daily_page_market_cluster_theme',
            }

            search_columns = set(
                connection.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.columns
                        WHERE table_schema = 'stock'
                          AND column_name = 'search_document'
                          AND table_name IN (
                              'market_daily_page',
                              'market_daily_page_market',
                              'market_daily_page_market_cluster'
                          )
                        """
                    )
                ).scalars()
            )
            assert search_columns == {
                'market_daily_page',
                'market_daily_page_market',
                'market_daily_page_market_cluster',
            }

            seed_counts = connection.execute(
                text(
                    """
                    WITH RECURSIVE theme_tree AS (
                        SELECT code, parent_code, 1 AS depth, ARRAY[code] AS path
                        FROM stock.theme_catalog
                        WHERE parent_code IS NULL
                        UNION ALL
                        SELECT child.code, child.parent_code,
                               theme_tree.depth + 1,
                               theme_tree.path || child.code
                        FROM theme_tree
                        JOIN stock.theme_catalog AS child
                          ON child.parent_code = theme_tree.code
                        WHERE NOT child.code = ANY(theme_tree.path)
                    )
                    SELECT count(*),
                           count(*) FILTER (WHERE depth = 1),
                           count(*) FILTER (WHERE depth = 2),
                           count(*) FILTER (WHERE depth = 3)
                    FROM theme_tree
                    """
                )
            ).one()
            assert seed_counts == (63, 5, 18, 40)

            active_seed_count = connection.execute(
                text(
                    """
                    SELECT count(*)
                    FROM stock.theme_catalog
                    WHERE is_active
                    """
                )
            ).scalar_one()
            assert active_seed_count == 63

            version = connection.execute(
                text('SELECT version_num FROM stock.alembic_version')
            ).scalar_one()
            assert version == ALEMBIC_HEAD

        assert (
            sum(
                rule.fallback_enabled
                for rule in load_theme_rules(CANONICAL_LEAF_CODES).values()
            )
            == 20
        )
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def _assert_article_similarity_contract(connection) -> None:
    def query(sql: str):
        if hasattr(connection, 'exec_driver_sql'):
            return connection.exec_driver_sql(sql)
        return connection.execute(sql)

    source_columns = query(
        """
            SELECT column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'stock'
              AND table_name = 'news_cluster'
              AND column_name IN (
                  'article_grouping_status',
                  'article_grouping_generated_at',
                  'article_grouping_issue_code'
            )
            ORDER BY column_name
            """
    ).fetchall()
    assert source_columns == [
        ('article_grouping_generated_at', 'YES', None),
        ('article_grouping_issue_code', 'YES', "'SIMILARITY_GROUPING_FAILED'::text"),
        ('article_grouping_status', 'NO', "'UNAVAILABLE'::text"),
    ]

    snapshot_columns = query(
        """
            SELECT table_name, column_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'stock'
              AND (
                  table_name = 'market_daily_page_market_cluster'
                  AND column_name IN (
                      'article_grouping_status',
                      'article_grouping_generated_at',
                      'article_grouping_issue_code',
                      'article_grouping_algorithm_version'
                  )
                  OR table_name = 'market_daily_page_article_link'
                  AND column_name IN (
                      'similar_group_rank',
                      'is_similar_group_representative',
                      'exact_duplicate_count'
                  )
            )
            ORDER BY table_name, column_name
            """
    ).fetchall()
    assert snapshot_columns == [
        (
            'market_daily_page_article_link',
            'exact_duplicate_count',
            'NO',
            '0',
        ),
        (
            'market_daily_page_article_link',
            'is_similar_group_representative',
            'NO',
            'true',
        ),
        (
            'market_daily_page_article_link',
            'similar_group_rank',
            'NO',
            '1',
        ),
        (
            'market_daily_page_market_cluster',
            'article_grouping_algorithm_version',
            'YES',
            None,
        ),
        (
            'market_daily_page_market_cluster',
            'article_grouping_generated_at',
            'YES',
            None,
        ),
        (
            'market_daily_page_market_cluster',
            'article_grouping_issue_code',
            'YES',
            "'SIMILARITY_GROUPING_FAILED'::text",
        ),
        (
            'market_daily_page_market_cluster',
            'article_grouping_status',
            'NO',
            "'UNAVAILABLE'::text",
        ),
    ]

    tables = [
        row[0]
        for row in query(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'stock'
              AND table_name IN (
                  'news_cluster_similar_group',
                  'news_cluster_similar_group_article'
              )
            ORDER BY table_name
            """
        ).fetchall()
    ]
    assert tables == [
        'news_cluster_similar_group',
        'news_cluster_similar_group_article',
    ]

    constraints = query(
        """
            SELECT conname, pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE connamespace = 'stock'::regnamespace
              AND conname IN (
                  'chk_news_cluster_article_grouping_status',
                  'chk_news_cluster_article_grouping_ready_generated',
                  'chk_page_market_cluster_grouping_status',
                  'chk_page_market_cluster_grouping_ready',
                  'chk_similar_group_rank_positive',
                  'chk_similar_group_article_exact_count_non_negative',
                  'chk_similar_group_article_rank_positive'
              )
            ORDER BY conname
            """
    ).fetchall()
    assert {name for name, _definition in constraints} == {
        'chk_page_market_cluster_grouping_ready',
        'chk_page_market_cluster_grouping_status',
        'chk_news_cluster_article_grouping_ready_generated',
        'chk_news_cluster_article_grouping_status',
        'chk_similar_group_article_exact_count_non_negative',
        'chk_similar_group_article_rank_positive',
        'chk_similar_group_rank_positive',
    }

    indexes = [
        row[0]
        for row in query(
            """
            SELECT indexname
            FROM pg_indexes
            WHERE schemaname = 'stock'
              AND indexname IN (
                  'idx_news_cluster_similar_group_cluster_rank',
                  'idx_news_cluster_similar_group_article_processed'
              )
            ORDER BY indexname
            """
        ).fetchall()
    ]
    assert indexes == [
        'idx_news_cluster_similar_group_article_processed',
    ]

    representative_fks = query(
        """
        SELECT source_namespace.nspname,
               source_table.relname,
               target_namespace.nspname,
               target_table.relname,
               ARRAY(
                   SELECT source_column.attname
                   FROM unnest(constraint_.conkey) WITH ORDINALITY AS source_key(attnum, ordinal)
                   JOIN pg_attribute AS source_column
                     ON source_column.attrelid = constraint_.conrelid
                    AND source_column.attnum = source_key.attnum
                   ORDER BY source_key.ordinal
               ),
               ARRAY(
                   SELECT target_column.attname
                   FROM unnest(constraint_.confkey) WITH ORDINALITY AS target_key(attnum, ordinal)
                   JOIN pg_attribute AS target_column
                     ON target_column.attrelid = constraint_.confrelid
                    AND target_column.attnum = target_key.attnum
                   ORDER BY target_key.ordinal
               ),
               constraint_.condeferrable,
               constraint_.condeferred
        FROM pg_constraint AS constraint_
        JOIN pg_class AS source_table
          ON source_table.oid = constraint_.conrelid
        JOIN pg_namespace AS source_namespace
          ON source_namespace.oid = source_table.relnamespace
        JOIN pg_class AS target_table
          ON target_table.oid = constraint_.confrelid
        JOIN pg_namespace AS target_namespace
          ON target_namespace.oid = target_table.relnamespace
        WHERE source_namespace.nspname = 'stock'
          AND constraint_.conname = 'fk_news_cluster_similar_group_representative_membership'
        """
    ).fetchall()
    assert representative_fks == [
        (
            'stock',
            'news_cluster_similar_group',
            'stock',
            'news_cluster_article',
            ['cluster_id', 'representative_article_id'],
            ['cluster_id', 'processed_article_id'],
            True,
            True,
        )
    ]

    key_constraints = query(
        """
        SELECT constraint_.conname,
               constraint_.contype,
               pg_get_constraintdef(constraint_.oid)
        FROM pg_constraint AS constraint_
        WHERE constraint_.connamespace = 'stock'::regnamespace
          AND constraint_.conrelid IN (
              'stock.news_cluster_similar_group'::regclass,
              'stock.news_cluster_similar_group_article'::regclass
          )
          AND constraint_.conname IN (
              'news_cluster_similar_group_pkey',
              'uq_news_cluster_similar_group_cluster_rank',
              'chk_similar_group_rank_positive',
              'news_cluster_similar_group_article_pkey',
              'uq_news_cluster_similar_group_article_group_rank',
              'chk_similar_group_article_exact_count_non_negative',
              'chk_similar_group_article_rank_positive'
          )
        ORDER BY constraint_.conname
        """
    ).fetchall()
    definitions = {
        name: (constraint_type, definition)
        for name, constraint_type, definition in key_constraints
    }
    assert definitions['news_cluster_similar_group_pkey'] == (
        'p',
        'PRIMARY KEY (id)',
    )
    assert definitions['uq_news_cluster_similar_group_cluster_rank'] == (
        'u',
        'UNIQUE (cluster_id, group_rank)',
    )
    assert definitions['chk_similar_group_rank_positive'] == (
        'c',
        'CHECK ((group_rank > 0))',
    )
    assert definitions['news_cluster_similar_group_article_pkey'] == (
        'p',
        'PRIMARY KEY (similar_group_id, processed_article_id)',
    )
    assert definitions['uq_news_cluster_similar_group_article_group_rank'] == (
        'u',
        'UNIQUE (similar_group_id, article_rank)',
    )
    assert definitions['chk_similar_group_article_exact_count_non_negative'] == (
        'c',
        'CHECK ((exact_duplicate_count >= 0))',
    )
    assert definitions['chk_similar_group_article_rank_positive'] == (
        'c',
        'CHECK ((article_rank > 0))',
    )

    extension_count = query(
        """
            SELECT count(*)
            FROM pg_extension
            WHERE extname = 'vector'
            """
    ).fetchone()[0]
    assert extension_count == 0


def test_article_similarity_sql_migration_is_idempotent_on_postgresql(
    postgres_connection,
):
    _execute_file(postgres_connection, SIMILARITY_MIGRATION)
    _execute_file(postgres_connection, SIMILARITY_MIGRATION)

    _assert_article_similarity_contract(postgres_connection)

    with postgres_connection.transaction():
        article_id = postgres_connection.execute(
            """
            INSERT INTO stock.news_article_processed (
                business_date,
                market_type,
                dedupe_hash,
                canonical_title,
                origin_link
            )
            VALUES (
                DATE '2026-08-20',
                'KR',
                repeat('a', 64),
                'Grouping test article',
                'https://example.test/grouping'
            )
            RETURNING id
            """
        ).fetchone()[0]
        cluster_id = postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster (
                business_date,
                market_type,
                cluster_rank,
                title,
                representative_article_id
            )
            VALUES (DATE '2026-08-20', 'KR', 1, 'Grouping test', %s)
            RETURNING id
            """,
            (article_id,),
        ).fetchone()[0]
        postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster_article (
                cluster_id,
                processed_article_id,
                article_rank
            )
            VALUES (%s, %s, 1)
            """,
            (cluster_id, article_id),
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        postgres_connection.execute(
            """
            UPDATE stock.news_cluster
            SET article_grouping_status = 'READY',
                article_grouping_generated_at = NULL
            WHERE business_date = DATE '2026-08-20'
            """
        )


@pytest.mark.anyio
async def test_exact_duplicate_counts_bind_multiple_ids_as_postgres_array(
    postgres_connection,
):
    processed_ids = []
    for index in range(4):
        processed_ids.append(
            postgres_connection.execute(
                """
                INSERT INTO stock.news_article_processed (
                    business_date,
                    market_type,
                    dedupe_hash,
                    canonical_title,
                    origin_link
                )
                VALUES (DATE '2026-08-21', 'US', %s, %s, %s)
                RETURNING id
                """,
                (
                    f'{index + 1:064d}',
                    f'Exact count article {index}',
                    f'https://example.test/exact-count/{index}',
                ),
            ).fetchone()[0]
        )
    raw_ids = []
    for index in range(5):
        raw_ids.append(
            postgres_connection.execute(
                """
                INSERT INTO stock.news_article_raw (
                    provider_name,
                    provider_article_key,
                    market_type,
                    title
                )
                VALUES ('TASK5_TEST', %s, 'US', %s)
                RETURNING id
                """,
                (f'exact-count-raw-{index}', f'Raw exact count {index}'),
            ).fetchone()[0]
        )
    for raw_id, processed_id in (
        (raw_ids[0], processed_ids[0]),
        (raw_ids[1], processed_ids[0]),
        (raw_ids[2], processed_ids[0]),
        (raw_ids[3], processed_ids[1]),
        (raw_ids[4], processed_ids[3]),
    ):
        postgres_connection.execute(
            """
            INSERT INTO stock.news_article_raw_processed_map (
                raw_article_id,
                processed_article_id
            )
            VALUES (%s, %s)
            """,
            (raw_id, processed_id),
        )

    database_url = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    async_database_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_async_engine(async_database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            result = await ArticleGroupRepository(session).get_exact_duplicate_counts(
                processed_ids
            )
            assert result == [
                ExactDuplicateCountRecord(processed_ids[0], 2),
                ExactDuplicateCountRecord(processed_ids[1], 0),
                ExactDuplicateCountRecord(processed_ids[2], 0),
                ExactDuplicateCountRecord(processed_ids[3], 0),
            ]
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_membership_replacement_invalidates_source_but_preserves_page_snapshot(
    postgres_connection,
):
    _execute_file(postgres_connection, SIMILARITY_MIGRATION)
    business_date = date(2026, 8, 22)
    with postgres_connection.transaction():
        processed_ids = []
        for index in range(2):
            processed_ids.append(
                postgres_connection.execute(
                    """
                    INSERT INTO stock.news_article_processed (
                        business_date,
                        market_type,
                        dedupe_hash,
                        canonical_title,
                        origin_link
                    )
                    VALUES (%s, 'US', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        business_date,
                        f'{index + 11:064d}',
                        f'Membership article {index}',
                        f'https://example.test/membership/{index}',
                    ),
                ).fetchone()[0]
            )
        cluster_id, cluster_uid = postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster (
                business_date,
                market_type,
                cluster_rank,
                title,
                representative_article_id
            )
            VALUES (%s, 'US', 1, 'Membership replacement', %s)
            RETURNING id, cluster_uid
            """,
            (business_date, processed_ids[0]),
        ).fetchone()
        for processed_id, article_rank in (
            (processed_ids[0], 1),
            (processed_ids[1], 2),
        ):
            postgres_connection.execute(
                """
                INSERT INTO stock.news_cluster_article (
                    cluster_id,
                    processed_article_id,
                    article_rank
                )
                VALUES (%s, %s, %s)
                """,
                (cluster_id, processed_id, article_rank),
            )
        old_group_id = postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster_similar_group (
                cluster_id,
                group_rank,
                representative_article_id,
                algorithm_version,
                generated_at
            )
            VALUES (%s, 1, %s, 'old-v1', TIMESTAMPTZ '2026-08-22 01:00:00+00')
            RETURNING id
            """,
            (cluster_id, processed_ids[1]),
        ).fetchone()[0]
        postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster_similar_group_article (
                similar_group_id,
                processed_article_id,
                similarity_score,
                exact_duplicate_count,
                is_representative,
                article_rank
            )
            VALUES (%s, %s, 1.0, 0, TRUE, 1)
            """,
            (old_group_id, processed_ids[1]),
        )
        postgres_connection.execute(
            """
            UPDATE stock.news_cluster
            SET article_grouping_status = 'READY',
                article_grouping_generated_at = TIMESTAMPTZ '2026-08-22 01:00:00+00',
                article_grouping_issue_code = NULL
            WHERE id = %s
            """,
            (cluster_id,),
        )
        job_id = postgres_connection.execute(
            """
            INSERT INTO stock.batch_job (business_date, status, trigger_type, run_mode)
            VALUES (%s, 'SUCCESS', 'MANUAL', 'FULL')
            RETURNING id
            """,
            (business_date,),
        ).fetchone()[0]
        page_id = postgres_connection.execute(
            """
            INSERT INTO stock.market_daily_page (
                business_date,
                version_no,
                page_title,
                status,
                batch_job_id
            )
            VALUES (%s, 1, 'Snapshot', 'READY', %s)
            RETURNING id
            """,
            (business_date, job_id),
        ).fetchone()[0]
        page_market_id = postgres_connection.execute(
            """
            INSERT INTO stock.market_daily_page_market (
                page_id,
                market_type,
                display_order,
                market_label
            )
            VALUES (%s, 'US', 1, 'US market')
            RETURNING id
            """,
            (page_id,),
        ).fetchone()[0]
        postgres_connection.execute(
            """
            INSERT INTO stock.market_daily_page_market_cluster (
                page_market_id,
                cluster_id,
                cluster_uid,
                display_order,
                title,
                article_count,
                representative_article_id,
                article_grouping_status,
                article_grouping_generated_at,
                article_grouping_issue_code
            )
            VALUES (
                %s, %s, %s, 1, 'Snapshot cluster', 2, %s,
                'READY', TIMESTAMPTZ '2026-08-22 01:00:00+00', NULL
            )
            """,
            (page_market_id, cluster_id, cluster_uid, processed_ids[0]),
        )

    database_url = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    async_database_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_async_engine(async_database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            await NewsClusterWriteRepository(session).replace_cluster_articles(
                cluster_id,
                [
                    NewsClusterArticleCreateParams(
                        cluster_id=cluster_id,
                        processed_article_id=processed_ids[0],
                        article_rank=1,
                    )
                ],
            )
            await session.commit()
            source = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT article_grouping_status,
                               article_grouping_generated_at,
                               article_grouping_issue_code
                        FROM stock.news_cluster
                        WHERE id = :cluster_id
                        """
                        ),
                        {'cluster_id': cluster_id},
                    )
                )
                .mappings()
                .one()
            )
            assert source == {
                'article_grouping_status': 'UNAVAILABLE',
                'article_grouping_generated_at': None,
                'article_grouping_issue_code': 'SIMILARITY_GROUPING_FAILED',
            }
            assert (
                await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM stock.news_cluster_similar_group
                        WHERE cluster_id = :cluster_id
                        """
                    ),
                    {'cluster_id': cluster_id},
                )
            ) == 0
            assert (
                await session.execute(
                    text(
                        """
                        SELECT processed_article_id
                        FROM stock.news_cluster_article
                        WHERE cluster_id = :cluster_id
                        ORDER BY article_rank
                        """
                    ),
                    {'cluster_id': cluster_id},
                )
            ).scalars().all() == [processed_ids[0]]
            page_state = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT page.status::text AS page_status,
                               cluster.article_grouping_status AS snapshot_status
                        FROM stock.market_daily_page page
                        JOIN stock.market_daily_page_market market
                          ON market.page_id = page.id
                        JOIN stock.market_daily_page_market_cluster cluster
                          ON cluster.page_market_id = market.id
                        WHERE cluster.cluster_id = :cluster_id
                        """
                        ),
                        {'cluster_id': cluster_id},
                    )
                )
                .mappings()
                .one()
            )
            assert page_state == {
                'page_status': 'READY',
                'snapshot_status': 'READY',
            }
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_group_replacement_rolls_back_partial_member_insert_on_postgres(
    postgres_connection,
):
    _execute_file(postgres_connection, SIMILARITY_MIGRATION)
    business_date = date(2026, 8, 23)
    with postgres_connection.transaction():
        processed_ids = []
        for index in range(2):
            processed_ids.append(
                postgres_connection.execute(
                    """
                    INSERT INTO stock.news_article_processed (
                        business_date,
                        market_type,
                        dedupe_hash,
                        canonical_title,
                        origin_link
                    )
                    VALUES (%s, 'US', %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        business_date,
                        f'{index + 21:064d}',
                        f'Rollback article {index}',
                        f'https://example.test/rollback/{index}',
                    ),
                ).fetchone()[0]
            )
        cluster_id = postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster (
                business_date,
                market_type,
                cluster_rank,
                title,
                representative_article_id
            )
            VALUES (%s, 'US', 1, 'Rollback cluster', %s)
            RETURNING id
            """,
            (business_date, processed_ids[0]),
        ).fetchone()[0]
        for processed_id, article_rank in (
            (processed_ids[0], 1),
            (processed_ids[1], 2),
        ):
            postgres_connection.execute(
                """
                INSERT INTO stock.news_cluster_article (
                    cluster_id,
                    processed_article_id,
                    article_rank
                )
                VALUES (%s, %s, %s)
                """,
                (cluster_id, processed_id, article_rank),
            )
        old_group_id = postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster_similar_group (
                cluster_id,
                group_rank,
                representative_article_id,
                algorithm_version,
                generated_at
            )
            VALUES (%s, 1, %s, 'old-v1', TIMESTAMPTZ '2026-08-23 01:00:00+00')
            RETURNING id
            """,
            (cluster_id, processed_ids[0]),
        ).fetchone()[0]
        postgres_connection.execute(
            """
            INSERT INTO stock.news_cluster_similar_group_article (
                similar_group_id,
                processed_article_id,
                similarity_score,
                exact_duplicate_count,
                is_representative,
                article_rank
            )
            VALUES (%s, %s, 1.0, 0, TRUE, 1)
            """,
            (old_group_id, processed_ids[0]),
        )
        postgres_connection.execute(
            """
            UPDATE stock.news_cluster
            SET article_grouping_status = 'READY',
                article_grouping_generated_at = TIMESTAMPTZ '2026-08-23 01:00:00+00',
                article_grouping_issue_code = NULL
            WHERE id = %s
            """,
            (cluster_id,),
        )

    database_url = os.environ['STOCKAPP_MIGRATION_TEST_DSN']
    async_database_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_async_engine(async_database_url)
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_maker() as session:
            grouping = SimilarityGroupingResult(
                groups=(
                    SimilarityGroup(
                        group_rank=1,
                        representative_article_id=processed_ids[0],
                        members=(
                            SimilarityGroupMember(
                                processed_article_id=processed_ids[0],
                                similarity_score=1.0,
                                is_representative=True,
                                article_rank=1,
                            ),
                        ),
                    ),
                    SimilarityGroup(
                        group_rank=2,
                        representative_article_id=processed_ids[1],
                        members=(
                            SimilarityGroupMember(
                                processed_article_id=processed_ids[1],
                                similarity_score=1.0,
                                is_representative=True,
                                article_rank=1,
                            ),
                        ),
                    ),
                )
            )
            with pytest.raises(DataError):
                await ArticleGroupRepository(session).replace_cluster_groups(
                    cluster_id,
                    grouping,
                    algorithm_version='new-v2',
                    generated_at=datetime(2026, 8, 23, 2, tzinfo=UTC),
                    exact_counts={processed_ids[0]: 0, processed_ids[1]: 2_147_483_648},
                )
            source = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT article_grouping_status,
                               article_grouping_generated_at,
                               article_grouping_issue_code
                        FROM stock.news_cluster
                        WHERE id = :cluster_id
                        """
                        ),
                        {'cluster_id': cluster_id},
                    )
                )
                .mappings()
                .one()
            )
            assert source['article_grouping_status'] == 'READY'
            assert source['article_grouping_generated_at'] is not None
            assert source['article_grouping_issue_code'] is None
            groups = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT id, algorithm_version
                        FROM stock.news_cluster_similar_group
                        WHERE cluster_id = :cluster_id
                        """
                        ),
                        {'cluster_id': cluster_id},
                    )
                )
                .mappings()
                .all()
            )
            assert groups == [{'id': old_group_id, 'algorithm_version': 'old-v1'}]
            members = (
                (
                    await session.execute(
                        text(
                            """
                        SELECT similar_group_id, processed_article_id
                        FROM stock.news_cluster_similar_group_article
                        WHERE similar_group_id = :group_id
                        """
                        ),
                        {'group_id': old_group_id},
                    )
                )
                .mappings()
                .all()
            )
            assert members == [
                {
                    'similar_group_id': old_group_id,
                    'processed_article_id': processed_ids[0],
                }
            ]
            await session.rollback()
    finally:
        await engine.dispose()


def test_alembic_fresh_upgrade_applies_article_similarity_revision():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, 'head')
        _run_alembic(engine, database_url, command.upgrade, 'head')

        with engine.connect() as connection:
            _assert_article_similarity_contract(connection)
            version = connection.execute(
                text('SELECT version_num FROM stock.alembic_version')
            ).scalar_one()
            assert version == ALEMBIC_HEAD
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_alembic_previous_head_upgrade_applies_article_similarity_revision():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(
            engine,
            database_url,
            command.upgrade,
            ALEMBIC_PAGE_SEARCH_REVISION,
        )
        _run_alembic(engine, database_url, command.upgrade, 'head')

        with engine.connect() as connection:
            _assert_article_similarity_contract(connection)
            version = connection.execute(
                text('SELECT version_num FROM stock.alembic_version')
            ).scalar_one()
            assert version == ALEMBIC_HEAD
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_alembic_head_backfills_and_pins_legacy_null_similarity_rank():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(
            engine,
            database_url,
            command.upgrade,
            ALEMBIC_SIMILARITY_REVISION,
        )

        with engine.connect() as connection:
            job_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.batch_job (
                        business_date,
                        status,
                        trigger_type,
                        run_mode
                    )
                    VALUES (DATE '2026-08-15', 'SUCCESS', 'MANUAL', 'FULL')
                    RETURNING id
                    """
                )
            ).scalar_one()
            page_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page (
                        business_date,
                        version_no,
                        page_title,
                        status,
                        batch_job_id
                    )
                    VALUES (DATE '2026-08-15', 1, 'Legacy page', 'READY', :job_id)
                    RETURNING id
                    """
                ),
                {'job_id': job_id},
            ).scalar_one()
            page_market_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page_market (
                        page_id,
                        market_type,
                        display_order,
                        market_label
                    )
                    VALUES (:page_id, 'KR', 1, 'KR market')
                    RETURNING id
                    """
                ),
                {'page_id': page_id},
            ).scalar_one()
            link_id = connection.execute(
                text(
                    """
                    INSERT INTO stock.market_daily_page_article_link (
                        page_market_id,
                        display_order,
                        title,
                        origin_link
                    )
                    VALUES (
                        :page_market_id,
                        1,
                        'Legacy link',
                        'https://example.test/legacy'
                    )
                    RETURNING id
                    """
                ),
                {'page_market_id': page_market_id},
            ).scalar_one()
            # The similarity revision leaves the column nullable and
            # defaultless, which is exactly how production rows written before
            # it ended up NULL.
            assert (
                connection.execute(
                    text(
                        'SELECT similar_group_rank '
                        'FROM stock.market_daily_page_article_link '
                        'WHERE id = :id'
                    ),
                    {'id': link_id},
                ).scalar_one()
                is None
            )
            connection.commit()

        _run_alembic(engine, database_url, command.upgrade, 'head')

        with engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        'SELECT similar_group_rank '
                        'FROM stock.market_daily_page_article_link '
                        'WHERE id = :id'
                    ),
                    {'id': link_id},
                ).scalar_one()
                == 1
            )
            _assert_article_similarity_contract(connection)
            version = connection.execute(
                text('SELECT version_num FROM stock.alembic_version')
            ).scalar_one()
            assert version == ALEMBIC_HEAD

        for display_order, rank_sql in ((2, 'NULL'), (3, '0')):
            with engine.connect() as connection:
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            f"""
                            INSERT INTO stock.market_daily_page_article_link (
                                page_market_id,
                                display_order,
                                title,
                                origin_link,
                                similar_group_rank
                            )
                            VALUES (
                                :page_market_id,
                                {display_order},
                                'Rejected link',
                                'https://example.test/rejected',
                                {rank_sql}
                            )
                            """
                        ),
                        {'page_market_id': page_market_id},
                    )
                connection.rollback()
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_startup_migration_stamps_existing_schema_and_applies_article_similarity():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    settings = Settings(
        _env_file=None,
        app_env='test',
        database_url=sqlalchemy_url,
        database_schema='stock',
        database_migration_enabled=True,
        database_migration_lock_timeout_seconds=5,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
            connection.exec_driver_sql(
                ALEMBIC_BASELINE_SQL.read_text(encoding='utf-8').replace('%', '%%')
            )
            connection.commit()

        migration_runner.run_startup_migrations(settings, engine=engine)
        migration_runner.run_startup_migrations(settings, engine=engine)

        with engine.connect() as connection:
            _assert_article_similarity_contract(connection)
            version = connection.execute(
                text('SELECT version_num FROM stock.alembic_version')
            ).scalar_one()
            assert version == ALEMBIC_HEAD
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()


def test_startup_migration_upgrades_stamped_previous_head_to_article_similarity():
    database_url = os.getenv('STOCKAPP_MIGRATION_TEST_DSN')
    if database_url is None:
        pytest.skip('STOCKAPP_MIGRATION_TEST_DSN is not configured.')

    sqlalchemy_url = database_url.replace(
        'postgresql://',
        'postgresql+psycopg://',
        1,
    )
    engine = create_engine(sqlalchemy_url, poolclass=pool.NullPool)
    settings = Settings(
        _env_file=None,
        app_env='test',
        database_url=sqlalchemy_url,
        database_schema='stock',
        database_migration_enabled=True,
        database_migration_lock_timeout_seconds=5,
    )
    try:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()

        _run_alembic(
            engine,
            database_url,
            command.upgrade,
            ALEMBIC_PAGE_SEARCH_REVISION,
        )
        with engine.connect() as connection:
            assert (
                connection.execute(
                    text('SELECT version_num FROM stock.alembic_version')
                ).scalar_one()
                == ALEMBIC_PAGE_SEARCH_REVISION
            )

        migration_runner.run_startup_migrations(settings, engine=engine)

        with engine.connect() as connection:
            _assert_article_similarity_contract(connection)
            assert (
                connection.execute(
                    text('SELECT version_num FROM stock.alembic_version')
                ).scalar_one()
                == ALEMBIC_HEAD
            )
    finally:
        with engine.connect() as connection:
            connection.execute(text('DROP SCHEMA IF EXISTS stock CASCADE'))
            connection.commit()
        engine.dispose()
