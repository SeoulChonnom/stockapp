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

PageSnapshotRepository = page_repo_module.PageSnapshotRepository


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
    assert 'ready' in sql.lower()
    assert ('distinct on' in sql.lower()) or ('row_number()' in sql.lower())
