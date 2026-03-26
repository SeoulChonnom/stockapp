from __future__ import annotations

from datetime import date

from app.api.deps import DbSession
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.schemas.page import ArchiveItemResponse, ArchiveListResponse, PaginationResponse


class ArchiveService:
    def __init__(self, repository: PageSnapshotRepository) -> None:
        self._repo = repository

    async def list_archive(
        self,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
        page: int,
        size: int,
    ) -> ArchiveListResponse:
        items = await self._repo.list_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=status,
            page=page,
            size=size,
        )
        total_count = await self._repo.count_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=status,
        )
        return ArchiveListResponse(
            items=[ArchiveItemResponse.model_validate(item) for item in items],
            pagination=PaginationResponse(page=page, size=size, totalCount=total_count),
        )


def get_archive_service(session: DbSession) -> ArchiveService:
    return ArchiveService(PageSnapshotRepository(session))


__all__ = ["ArchiveService", "get_archive_service"]
