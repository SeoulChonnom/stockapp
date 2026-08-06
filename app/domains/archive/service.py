from __future__ import annotations

from datetime import date

from app.core.exceptions import ValidationError
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.archive.assembler import build_archive_list_payload

ARCHIVE_STATUSES = frozenset({'READY', 'PARTIAL', 'FAILED'})


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
        normalized_status = status.upper() if status is not None else None
        if normalized_status is not None and normalized_status not in ARCHIVE_STATUSES:
            raise ValidationError(
                'UNSUPPORTED_ARCHIVE_STATUS', f'Unsupported archive status: {status}'
            )
        items = await self._repo.list_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=normalized_status,
            page=page,
            size=size,
        )
        total_count = await self._repo.count_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=normalized_status,
        )
        return build_archive_list_payload(
            items,
            page=page,
            size=size,
            total_count=total_count,
        )


__all__ = ['ArchiveService']
