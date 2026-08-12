from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from app.core.exceptions import NotFoundError
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.pages.assembler import build_daily_page_payload


class PagesService:
    def __init__(self, repository: PageSnapshotRepository) -> None:
        self._repo = repository

    async def get_latest_page(self) -> dict[str, Any]:
        page = await self._repo.get_latest_public_page_header()
        if page is None:
            raise NotFoundError(
                'LATEST_PAGE_NOT_FOUND', '가장 최근 생성된 페이지가 존재하지 않습니다.'
            )
        page = {**page, 'is_latest': page.get('is_latest', True)}
        return await self._build_page(page)

    async def get_page_by_date(
        self,
        business_date: date,
        version_no: int | None = None,
    ) -> dict[str, Any]:
        page = await self._repo.get_page_header_by_business_date(
            business_date, version_no
        )
        if page is None:
            if (
                version_no is not None
                and await self._repo.exists_page_for_business_date(business_date)
            ):
                raise NotFoundError(
                    'PAGE_VERSION_NOT_FOUND',
                    '요청한 페이지 버전이 존재하지 않습니다.',
                )
            raise NotFoundError(
                'PAGE_NOT_FOUND', '요청한 날짜의 페이지가 존재하지 않습니다.'
            )
        page = await self._ensure_latest_flag(page)
        return await self._build_page(page)

    async def get_page_by_id(self, page_id: int) -> dict[str, Any]:
        page = await self._repo.get_page_header_by_id(page_id)
        if page is None:
            raise NotFoundError('PAGE_NOT_FOUND', '요청한 페이지를 찾을 수 없습니다.')
        page = await self._ensure_latest_flag(page)
        return await self._build_page(page)

    async def get_date_navigation(
        self, business_date: date
    ) -> dict[str, date | bool | None]:
        page_exists = await self._repo.exists_public_page_for_business_date(
            business_date
        )
        neighbors = await self._repo.get_adjacent_public_business_dates(business_date)
        return {
            'businessDate': business_date,
            'pageExists': page_exists,
            'previousBusinessDate': neighbors['previous_business_date'],
            'nextBusinessDate': neighbors['next_business_date'],
        }

    async def _ensure_latest_flag(self, page: dict[str, Any]) -> dict[str, Any]:
        if 'is_latest' in page:
            return page
        latest_version_no = await self._repo.get_latest_version_no(
            page['business_date']
        )
        return {**page, 'is_latest': latest_version_no == page['version_no']}

    async def _build_page(self, page: dict[str, Any]) -> dict[str, Any]:
        page_id = page['id']
        business_date = page['business_date']
        markets = await self._repo.get_page_markets(page_id)
        market_ids = [row['id'] for row in markets]
        (
            indices,
            clusters,
            article_links,
            neighbors,
            versions,
        ) = await asyncio.gather(
            self._repo.get_page_indices(market_ids),
            self._repo.get_page_clusters(market_ids),
            self._repo.get_page_article_links(market_ids),
            self._repo.get_adjacent_public_business_dates(business_date),
            self._repo.list_page_versions(business_date),
        )
        return build_daily_page_payload(
            page,
            markets,
            indices,
            clusters,
            article_links,
            neighbors=neighbors,
            versions=versions,
        )


__all__ = ['PagesService']
