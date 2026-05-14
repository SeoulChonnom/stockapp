from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, UserDep
from app.core.response import ApiSuccess
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.archive.assembler import assemble_archive_list_response
from app.domains.archive.service import ArchiveService
from app.schemas.page import ArchiveListResponse

router = APIRouter(prefix='/pages', tags=['archive'])


def get_archive_service(session: DbSession) -> ArchiveService:
    return ArchiveService(PageSnapshotRepository(session))


ArchiveServiceDep = Annotated[ArchiveService, Depends(get_archive_service)]


@router.get('/archive', response_model=ApiSuccess[ArchiveListResponse])
async def list_archive(
    _: UserDep,
    service: ArchiveServiceDep,
    fromDate: Annotated[date | None, Query(alias='fromDate')] = None,
    toDate: Annotated[date | None, Query(alias='toDate')] = None,
    status: Annotated[str | None, Query(alias='status')] = None,
    page: Annotated[int, Query(alias='page', ge=1)] = 1,
    size: Annotated[int, Query(alias='size', ge=1, le=100)] = 30,
) -> ApiSuccess[ArchiveListResponse]:
    payload = await service.list_archive(
        from_date=fromDate,
        to_date=toDate,
        status=status,
        page=page,
        size=size,
    )
    return ApiSuccess(data=assemble_archive_list_response(payload))


__all__ = ['get_archive_service', 'router']
