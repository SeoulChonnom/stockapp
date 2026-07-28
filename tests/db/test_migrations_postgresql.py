from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.batch.ai_retry.resolver import resolve_effective_summaries
from app.db.repositories.ai_retry_repo import PostgresAiRetryRepository
from app.db.repositories.ai_summary_repo import AiSummaryRepository
from app.db.repositories.batch_job_repo import BatchJobRepository
from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.projections import BatchJobMarketContextCreateParams

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


def _execute_file(connection, path: Path) -> None:
    connection.execute(path.read_text(encoding='utf-8'))


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

    assert constraints == [('UNIQUE (business_date, dedupe_hash)',)]
    assert keywords == [
        ('KR', '코스피', True),
        ('US', '미국 증시', True),
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
            DROP CONSTRAINT uq_news_article_raw_business_provider_key,
            ADD CONSTRAINT uq_news_article_raw_provider_key
                UNIQUE (provider_name, provider_article_key);

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
