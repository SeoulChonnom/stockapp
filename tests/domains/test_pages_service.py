from __future__ import annotations

import asyncio
from datetime import date

import pytest

from tests.support import BUSINESS_DATE, jsonable, load_module

pages_service_module = load_module('app.domains.pages.service')
archive_service_module = load_module('app.domains.archive.service')

PagesService = pages_service_module.PagesService
ArchiveService = archive_service_module.ArchiveService


class FakePageSnapshotRepository:
    def __init__(
        self,
        *,
        page_header,
        markets,
        indices,
        clusters,
        article_links,
        archive_items,
        archive_total_count,
        adjacent_business_dates,
        page_versions,
    ):
        self.page_header = page_header
        self.adjacent_business_dates = adjacent_business_dates
        self.page_versions = page_versions
        self.markets = markets
        self.indices = indices
        self.clusters = clusters
        self.article_links = article_links
        self.archive_items = archive_items
        self.archive_total_count = archive_total_count
        self.has_page_for_date = True
        self.concurrent_detail_calls = 0
        self.max_concurrent_detail_calls = 0
        self.calls: list[tuple] = []

    async def get_latest_page_header(self):
        self.calls.append(('get_latest_page_header',))
        return self.page_header

    async def get_page_header_by_business_date(self, business_date, version_no=None):
        self.calls.append(
            ('get_page_header_by_business_date', business_date, version_no)
        )
        if version_no == 999:
            return None
        return self.page_header

    async def exists_page_for_business_date(self, business_date):
        self.calls.append(('exists_page_for_business_date', business_date))
        return self.has_page_for_date

    async def get_latest_version_no(self, business_date):
        self.calls.append(('get_latest_version_no', business_date))
        return self.page_header['version_no']

    async def get_page_header_by_id(self, page_id):
        self.calls.append(('get_page_header_by_id', page_id))
        return self.page_header if page_id == self.page_header['id'] else None

    async def get_page_markets(self, page_id):
        self.calls.append(('get_page_markets', page_id))
        return self.markets

    async def get_page_indices(self, page_market_ids):
        self.concurrent_detail_calls += 1
        self.max_concurrent_detail_calls = max(
            self.max_concurrent_detail_calls, self.concurrent_detail_calls
        )
        await asyncio.sleep(0)
        self.calls.append(('get_page_indices', tuple(page_market_ids)))
        self.concurrent_detail_calls -= 1
        return self.indices

    async def get_page_clusters(self, page_market_ids):
        self.concurrent_detail_calls += 1
        self.max_concurrent_detail_calls = max(
            self.max_concurrent_detail_calls, self.concurrent_detail_calls
        )
        await asyncio.sleep(0)
        self.calls.append(('get_page_clusters', tuple(page_market_ids)))
        self.concurrent_detail_calls -= 1
        return self.clusters

    async def get_page_article_links(self, page_market_ids):
        self.concurrent_detail_calls += 1
        self.max_concurrent_detail_calls = max(
            self.max_concurrent_detail_calls, self.concurrent_detail_calls
        )
        await asyncio.sleep(0)
        self.calls.append(('get_page_article_links', tuple(page_market_ids)))
        self.concurrent_detail_calls -= 1
        return self.article_links

    async def get_adjacent_business_dates(self, business_date):
        self.concurrent_detail_calls += 1
        self.max_concurrent_detail_calls = max(
            self.max_concurrent_detail_calls, self.concurrent_detail_calls
        )
        await asyncio.sleep(0)
        self.calls.append(('get_adjacent_business_dates', business_date))
        self.concurrent_detail_calls -= 1
        return self.adjacent_business_dates

    async def list_page_versions(self, business_date, *, limit=20):
        self.concurrent_detail_calls += 1
        self.max_concurrent_detail_calls = max(
            self.max_concurrent_detail_calls, self.concurrent_detail_calls
        )
        await asyncio.sleep(0)
        self.calls.append(('list_page_versions', business_date, limit))
        self.concurrent_detail_calls -= 1
        return self.page_versions

    async def list_archive_page_headers(self, **kwargs):
        self.calls.append(('list_archive_page_headers', kwargs))
        return self.archive_items

    async def count_archive_page_headers(self, **kwargs):
        self.calls.append(('count_archive_page_headers', kwargs))
        return self.archive_total_count


@pytest.fixture
def page_repository(
    sample_page_snapshot_row,
    sample_page_market_rows,
    sample_page_index_rows,
    sample_page_cluster_rows,
    sample_page_article_link_rows,
    sample_archive_list_payload,
    sample_adjacent_business_dates_row,
    sample_page_version_rows,
):
    return FakePageSnapshotRepository(
        page_header=sample_page_snapshot_row,
        markets=sample_page_market_rows,
        indices=sample_page_index_rows,
        clusters=sample_page_cluster_rows,
        article_links=sample_page_article_link_rows,
        archive_items=sample_archive_list_payload['items'],
        archive_total_count=sample_archive_list_payload['pagination']['totalCount'],
        adjacent_business_dates=sample_adjacent_business_dates_row,
        page_versions=sample_page_version_rows,
    )


@pytest.mark.anyio
async def test_pages_service_fetches_latest_page_bundle(
    page_repository, sample_daily_page_payload
):
    service = PagesService(page_repository)

    result = await service.get_latest_page()
    assert isinstance(result, dict)
    payload = jsonable(result)

    assert payload['pageId'] == sample_daily_page_payload['pageId']
    assert payload['markets'][0]['marketType'] == 'US'
    assert (
        payload['markets'][0]['topClusters'][0]['clusterId']
        == sample_daily_page_payload['markets'][0]['topClusters'][0]['clusterId']
    )
    assert [call[0] for call in page_repository.calls[:2]] == [
        'get_latest_page_header',
        'get_page_markets',
    ]
    assert {
        'get_page_indices',
        'get_page_clusters',
        'get_page_article_links',
    } <= {call[0] for call in page_repository.calls}
    assert page_repository.max_concurrent_detail_calls > 1


@pytest.mark.anyio
async def test_pages_service_uses_versioned_lookup_when_date_is_explicit(
    page_repository, sample_daily_page_payload
):
    service = PagesService(page_repository)

    result = await service.get_page_by_date(BUSINESS_DATE, version_no=3)
    assert isinstance(result, dict)
    payload = jsonable(result)

    assert payload['businessDate'] == sample_daily_page_payload['businessDate']
    assert page_repository.calls[0] == (
        'get_page_header_by_business_date',
        BUSINESS_DATE,
        3,
    )
    assert payload['metadata']['isLatest'] is True


@pytest.mark.anyio
async def test_pages_service_distinguishes_missing_page_version(page_repository):
    service = PagesService(page_repository)

    with pytest.raises(pages_service_module.NotFoundError) as exc_info:
        await service.get_page_by_date(BUSINESS_DATE, version_no=999)

    assert exc_info.value.code == 'PAGE_VERSION_NOT_FOUND'
    assert page_repository.calls == [
        ('get_page_header_by_business_date', BUSINESS_DATE, 999),
        ('exists_page_for_business_date', BUSINESS_DATE),
    ]


@pytest.mark.anyio
async def test_pages_service_uses_nearest_existing_dates_across_calendar_gap(
    page_repository,
):
    """2026-03-13 -> 2026-03-17 is a four-day gap; ±1 day would 404."""
    service = PagesService(page_repository)

    payload = jsonable(await service.get_page_by_date(BUSINESS_DATE))

    assert payload['navigation'] == {
        'previousBusinessDate': '2026-03-13',
        'nextBusinessDate': None,
    }
    assert payload['businessDate'] == '2026-03-17'
    assert ('get_adjacent_business_dates', BUSINESS_DATE) in page_repository.calls


@pytest.mark.anyio
async def test_pages_service_reports_null_navigation_for_only_page(page_repository):
    page_repository.adjacent_business_dates = {
        'previous_business_date': None,
        'next_business_date': None,
    }
    service = PagesService(page_repository)

    payload = jsonable(await service.get_page_by_date(BUSINESS_DATE))

    assert payload['navigation'] == {
        'previousBusinessDate': None,
        'nextBusinessDate': None,
    }


@pytest.mark.anyio
async def test_pages_service_reports_both_neighbors_when_present(page_repository):
    page_repository.adjacent_business_dates = {
        'previous_business_date': date(2026, 3, 13),
        'next_business_date': date(2026, 3, 18),
    }
    service = PagesService(page_repository)

    payload = jsonable(await service.get_page_by_id(501))

    assert payload['navigation'] == {
        'previousBusinessDate': '2026-03-13',
        'nextBusinessDate': '2026-03-18',
    }


@pytest.mark.anyio
async def test_latest_page_has_no_next_business_date(page_repository):
    service = PagesService(page_repository)

    payload = jsonable(await service.get_latest_page())

    assert payload['navigation']['nextBusinessDate'] is None
    assert payload['navigation']['previousBusinessDate'] == '2026-03-13'


@pytest.mark.anyio
async def test_pages_service_exposes_every_version_newest_first(page_repository):
    service = PagesService(page_repository)

    payload = jsonable(await service.get_page_by_date(BUSINESS_DATE))

    assert [version['versionNo'] for version in payload['versions']] == [3, 2, 1]
    assert [version['pageId'] for version in payload['versions']] == [501, 500, 499]
    assert [version['isLatest'] for version in payload['versions']] == [
        True,
        False,
        False,
    ]
    assert payload['versions'][1]['status'] == 'PARTIAL'
    assert payload['versions'][0]['generatedAt'] == '2026-03-18T06:12:10Z'
    assert ('list_page_versions', BUSINESS_DATE, 20) in page_repository.calls


@pytest.mark.anyio
async def test_navigation_and_versions_share_the_page_detail_round_trip(
    page_repository,
):
    service = PagesService(page_repository)

    await service.get_page_by_date(BUSINESS_DATE)

    assert page_repository.max_concurrent_detail_calls > 3


@pytest.mark.anyio
async def test_archive_service_returns_paged_summary(page_repository):
    service = ArchiveService(page_repository)

    result = await service.list_archive(
        from_date=date(2026, 3, 16),
        to_date=date(2026, 3, 17),
        status='READY',
        page=1,
        size=30,
    )
    assert isinstance(result, dict)
    payload = jsonable(result)

    assert payload['items'][0]['businessDate'] == '2026-03-17'
    assert payload['pagination']['totalCount'] == 2
    assert page_repository.calls[-2][0] == 'list_archive_page_headers'
    assert page_repository.calls[-1][0] == 'count_archive_page_headers'
