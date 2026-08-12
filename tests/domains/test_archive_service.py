from __future__ import annotations

from datetime import date

import pytest

from tests.support import load_module

archive_service_module = load_module('app.domains.archive.service')

ArchiveService = archive_service_module.ArchiveService
ValidationError = archive_service_module.ValidationError


class RecordingArchiveRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def list_archive_page_headers(self, **kwargs: object) -> list[dict]:
        self.calls.append(('list_archive_page_headers', kwargs))
        return []

    async def count_archive_page_headers(self, **kwargs: object) -> int:
        self.calls.append(('count_archive_page_headers', kwargs))
        return 0


@pytest.mark.anyio
async def test_archive_service_rejects_failed_status_before_querying_repository():
    repository = RecordingArchiveRepository()
    service = ArchiveService(repository)

    with pytest.raises(ValidationError) as exc_info:
        await service.list_archive(
            from_date=date(2026, 3, 16),
            to_date=date(2026, 3, 17),
            status='FAILED',
            page=1,
            size=30,
        )

    assert exc_info.value.code == 'UNSUPPORTED_ARCHIVE_STATUS'
    assert repository.calls == []
