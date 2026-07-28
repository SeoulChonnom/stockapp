from __future__ import annotations

import os
from pathlib import Path

import pytest

psycopg = pytest.importorskip('psycopg')

REPOSITORY_ROOT = Path(__file__).parents[2]
SCHEMA_SQL = REPOSITORY_ROOT / 'db' / 'schema_postgresql.sql'
MIGRATIONS_DIRECTORY = REPOSITORY_ROOT / 'db' / 'migrations'
DATE_DEDUPE_MIGRATION = (
    MIGRATIONS_DIRECTORY / '20260728_03_news_article_processed_date_dedupe.sql'
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
