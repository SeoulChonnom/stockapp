from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from app.db.repositories.market_context_repo import MarketContextRepository
from app.db.repositories.projections import BatchJobMarketContextCreateParams
from tests.support import DummyResult, RecordingAsyncSession, normalize_sql


@pytest.mark.anyio
async def test_market_context_insert_is_idempotent_and_window_is_immutable():
    session = RecordingAsyncSession()
    repository = MarketContextRepository(session)
    params = BatchJobMarketContextCreateParams(
        batch_job_id=1001,
        market_type='US',
        expected_session_date=date(2026, 7, 27),
        session_close_at=datetime(2026, 7, 27, 20, 0, tzinfo=UTC),
        news_window_start_at=datetime(2026, 7, 27, 0, 0, tzinfo=UTC),
        news_window_end_at=datetime(2026, 7, 28, 0, 0, tzinfo=UTC),
    )

    await repository.insert_if_absent(params)

    sql = normalize_sql(session.statements[0]).lower()
    assert 'on conflict (batch_job_id, market_type) do nothing' in sql
    assert 'do update' not in sql
    assert session.parameters[0]['news_window_start_at'] == params.news_window_start_at
    assert session.parameters[0]['news_window_end_at'] == params.news_window_end_at


@pytest.mark.anyio
async def test_coverage_watermark_only_reads_complete_windows_before_cutoff():
    watermark = datetime(2026, 7, 28, 0, 0, tzinfo=UTC)
    session = RecordingAsyncSession(results=[DummyResult([watermark])])
    repository = MarketContextRepository(session)
    cutoff = datetime(2026, 7, 29, 0, 0, tzinfo=UTC)

    result = await repository.get_latest_complete_coverage_end(
        market_type='KR',
        at_or_before=cutoff,
    )

    assert result == watermark
    sql = normalize_sql(session.statements[0]).lower()
    assert 'news_coverage_complete' in sql
    assert 'news_window_end_at <= ' in sql
    assert 'order by news_window_end_at desc' in sql


def test_market_session_migration_contains_legacy_safe_snapshot_columns():
    migration = Path(
        'db/migrations/20260729_05_market_session_context_source_date.sql'
    ).read_text()
    normalized = ' '.join(migration.lower().split())

    assert normalized.startswith('begin;')
    assert normalized.endswith('commit;')
    assert 'create table if not exists stock.batch_job_market_context' in normalized
    assert 'add column if not exists source_date date null' in normalized
    assert 'add column if not exists expected_session_date date null' in normalized
    assert (
        'add column if not exists news_window_start_at timestamptz null' in normalized
    )
    assert 'uq_news_article_raw_business_provider_key' in normalized


def test_schema_source_truth_requires_context_for_new_rows():
    schema = Path('db/schema_postgresql.sql').read_text()
    normalized = ' '.join(schema.lower().split())
    context_table = normalized.split(
        'create table batch_job_market_context', maxsplit=1
    )[1].split(');', maxsplit=1)[0]

    assert 'expected_session_date date not null' in context_table
    assert 'session_close_at timestamptz not null' in context_table
    assert 'news_window_start_at timestamptz not null' in context_table
    assert 'news_window_end_at timestamptz not null' in context_table
    assert 'news_coverage_complete boolean not null default false' in context_table
