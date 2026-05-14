from __future__ import annotations

from datetime import date

from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.archive.assembler import build_archive_list_payload


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
    ) -> dict[str, object]:
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
        return build_archive_list_payload(
            items,
            page=page,
            size=size,
            total_count=total_count,
        )


__all__ = ['ArchiveService']
