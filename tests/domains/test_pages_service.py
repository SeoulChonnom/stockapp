from __future__ import annotations

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
    ):
        self.page_header = page_header
        self.markets = markets
        self.indices = indices
        self.clusters = clusters
        self.article_links = article_links
        self.archive_items = archive_items
        self.archive_total_count = archive_total_count
        self.calls: list[tuple] = []

    async def get_latest_page_header(self):
        self.calls.append(('get_latest_page_header',))
        return self.page_header

    async def get_page_header_by_business_date(self, business_date, version_no=None):
        self.calls.append(
            ('get_page_header_by_business_date', business_date, version_no)
        )
        return self.page_header

    async def get_page_header_by_id(self, page_id):
        self.calls.append(('get_page_header_by_id', page_id))
        return self.page_header if page_id == self.page_header['id'] else None

    async def get_page_markets(self, page_id):
        self.calls.append(('get_page_markets', page_id))
        return self.markets

    async def get_page_indices(self, page_market_ids):
        self.calls.append(('get_page_indices', tuple(page_market_ids)))
        return self.indices

    async def get_page_clusters(self, page_market_ids):
        self.calls.append(('get_page_clusters', tuple(page_market_ids)))
        return self.clusters

    async def get_page_article_links(self, page_market_ids):
        self.calls.append(('get_page_article_links', tuple(page_market_ids)))
        return self.article_links

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
):
    return FakePageSnapshotRepository(
        page_header=sample_page_snapshot_row,
        markets=sample_page_market_rows,
        indices=sample_page_index_rows,
        clusters=sample_page_cluster_rows,
        article_links=sample_page_article_link_rows,
        archive_items=sample_archive_list_payload['items'],
        archive_total_count=sample_archive_list_payload['pagination']['totalCount'],
    )


@pytest.mark.anyio
async def test_pages_service_fetches_latest_page_bundle(
    page_repository, sample_daily_page_payload
):
    service = PagesService(page_repository)

    result = await service.get_latest_page()
    payload = jsonable(result)

    assert payload['pageId'] == sample_daily_page_payload['pageId']
    assert payload['markets'][0]['marketType'] == 'US'
    assert (
        payload['markets'][0]['topClusters'][0]['clusterId']
        == sample_daily_page_payload['markets'][0]['topClusters'][0]['clusterId']
    )
    assert [call[0] for call in page_repository.calls[:5]] == [
        'get_latest_page_header',
        'get_page_markets',
        'get_page_indices',
        'get_page_clusters',
        'get_page_article_links',
    ]


@pytest.mark.anyio
async def test_pages_service_uses_versioned_lookup_when_date_is_explicit(
    page_repository, sample_daily_page_payload
):
    service = PagesService(page_repository)

    result = await service.get_page_by_date(BUSINESS_DATE, version_no=3)
    payload = jsonable(result)

    assert payload['businessDate'] == sample_daily_page_payload['businessDate']
    assert page_repository.calls[0] == (
        'get_page_header_by_business_date',
        BUSINESS_DATE,
        3,
    )


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
    payload = jsonable(result)

    assert payload['items'][0]['businessDate'] == '2026-03-17'
    assert payload['pagination']['totalCount'] == 2
    assert page_repository.calls[-2][0] == 'list_archive_page_headers'
    assert page_repository.calls[-1][0] == 'count_archive_page_headers'
