from __future__ import annotations

from datetime import date
from typing import Any

from app.core.exceptions import NotFoundError
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.pages.assembler import build_daily_page_payload


class PagesService:
    def __init__(self, repository: PageSnapshotRepository) -> None:
        self._repo = repository

    async def get_latest_page(self) -> dict[str, Any]:
        page = await self._repo.get_latest_page_header()
        if page is None:
            raise NotFoundError(
                'LATEST_PAGE_NOT_FOUND', '가장 최근 생성된 페이지가 존재하지 않습니다.'
            )
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
            raise NotFoundError(
                'PAGE_NOT_FOUND', '요청한 날짜의 페이지가 존재하지 않습니다.'
            )
        return await self._build_page(page)

    async def get_page_by_id(self, page_id: int) -> dict[str, Any]:
        page = await self._repo.get_page_header_by_id(page_id)
        if page is None:
            raise NotFoundError('PAGE_NOT_FOUND', '요청한 페이지를 찾을 수 없습니다.')
        return await self._build_page(page)

    async def _build_page(self, page: dict[str, Any]) -> dict[str, Any]:
        page_id = page['id']
        markets = await self._repo.get_page_markets(page_id)
        market_ids = [row['id'] for row in markets]
        indices = await self._repo.get_page_indices(market_ids)
        clusters = await self._repo.get_page_clusters(market_ids)
        article_links = await self._repo.get_page_article_links(market_ids)
        return build_daily_page_payload(
            page, markets, indices, clusters, article_links
        )


__all__ = ['PagesService']
