from __future__ import annotations

from datetime import date

import pytest

pytest.importorskip('sqlalchemy')

from tests.support import (
    DummyResult,
    RecordingAsyncSession,
    jsonable,
    load_module,
    normalize_sql,
)

page_repo_module = load_module('app.db.repositories.page_snapshot_repo')
page_write_repo_module = load_module('app.db.repositories.page_snapshot_write_repo')

PageSnapshotRepository = page_repo_module.PageSnapshotRepository
PageSnapshotWriteRepository = page_write_repo_module.PageSnapshotWriteRepository


@pytest.mark.anyio
async def test_get_latest_page_header_orders_by_latest_business_date_then_version(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_latest_page_header()

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    assert len(session.statements) == 1
    sql = normalize_sql(session.statements[0])
    assert 'market_daily_page' in sql
    assert 'business_date desc' in sql
    assert 'version_no desc' in sql
    assert 'limit 1' in sql


@pytest.mark.anyio
async def test_get_page_header_by_business_date_uses_explicit_version_when_provided(
    sample_page_snapshot_row,
):
    session = RecordingAsyncSession(results=[DummyResult([sample_page_snapshot_row])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_header_by_business_date(
        sample_page_snapshot_row['business_date'], version_no=3
    )

    assert jsonable(result)['id'] == sample_page_snapshot_row['id']
    sql = normalize_sql(session.statements[0])
    assert 'business_date' in sql
    assert 'version_no' in sql
    assert 'order by' in sql
    assert 'is_latest' in sql


@pytest.mark.anyio
async def test_get_page_indices_includes_source_daily_index_id():
    session = RecordingAsyncSession(results=[DummyResult([])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_page_indices([901])

    assert result == []
    sql = normalize_sql(session.statements[0])
    assert 'market_index_daily_id' in sql
    assert 'source_date' in sql
    assert 'expected_session_date' in sql
    assert 'session_close_at' in sql


@pytest.mark.anyio
async def test_exists_page_for_business_date_checks_date_boundary(sample_business_date):
    session = RecordingAsyncSession(results=[DummyResult([1])])
    repo = PageSnapshotRepository(session)

    result = await repo.exists_page_for_business_date(sample_business_date)

    assert result is True
    sql = normalize_sql(session.statements[0])
    assert 'select 1' in sql.lower()
    assert 'business_date' in sql


@pytest.mark.anyio
async def test_get_latest_version_no_returns_latest_version(sample_business_date):
    session = RecordingAsyncSession(results=[DummyResult([3])])
    repo = PageSnapshotRepository(session)

    result = await repo.get_latest_version_no(sample_business_date)

    assert result == 3
    sql = normalize_sql(session.statements[0])
    assert 'max(version_no)' in sql.lower()


@pytest.mark.anyio
async def test_get_next_version_no_takes_advisory_lock_before_allocating_version(
    sample_business_date,
):
    session = RecordingAsyncSession(results=[DummyResult([None]), DummyResult([4])])
    repo = PageSnapshotWriteRepository(session)

    result = await repo.get_next_version_no(sample_business_date)

    assert result == 4
    assert len(session.statements) == 2
    lock_sql = normalize_sql(session.statements[0]).lower()
    allocation_sql = normalize_sql(session.statements[1]).lower()
    assert 'pg_advisory_xact_lock' in lock_sql
    assert 'max(version_no)' not in lock_sql
    assert 'max(version_no)' in allocation_sql
    assert session.parameters[0] == {
        'lock_key': page_write_repo_module._page_version_lock_key(sample_business_date)
    }
    assert session.parameters[1] == {'business_date': sample_business_date}


@pytest.mark.anyio
async def test_list_archive_page_headers_prefers_latest_version_per_day(
    sample_archive_list_payload,
):
    session = RecordingAsyncSession(
        results=[DummyResult(sample_archive_list_payload['items'])]
    )
    repo = PageSnapshotRepository(session)

    result = await repo.list_archive_page_headers(
        from_date=date(2026, 3, 16),
        to_date=date(2026, 3, 17),
        status='READY',
        page=1,
        size=30,
    )

    assert [item['pageId'] for item in jsonable(result)] == [501, 502]
    sql = normalize_sql(session.statements[0])
    assert 'business_date' in sql
    assert 'status = cast(upper(' in sql.lower()
    assert ('distinct on' in sql.lower()) or ('row_number()' in sql.lower())


@pytest.mark.anyio
async def test_archive_status_filter_is_bound_at_uppercase_boundary(
    sample_archive_list_payload,
):
    session = RecordingAsyncSession(
        results=[DummyResult(sample_archive_list_payload['items'])]
    )
    repo = PageSnapshotRepository(session)

    await repo.list_archive_page_headers(status='ready')

    sql = normalize_sql(session.statements[0])
    assert 'upper(' in sql.lower()
