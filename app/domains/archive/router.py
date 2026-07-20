from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps.auth import CurrentUser, require_roles
from app.api.deps.db import get_db_session
from app.core.response import ApiSuccess
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.archive.assembler import assemble_archive_list_response
from app.domains.archive.service import ArchiveService
from app.schemas.page import ArchiveListResponse

router = APIRouter(prefix='/pages', tags=['archive'])
type DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]
type UserDep = Annotated[CurrentUser, Depends(require_roles('USER', 'ADMIN'))]


def get_archive_service(session: DbSessionDep) -> ArchiveService:
    return ArchiveService(PageSnapshotRepository(session))


type ArchiveServiceDep = Annotated[ArchiveService, Depends(get_archive_service)]
type ArchiveStatus = Literal['READY', 'PARTIAL', 'FAILED']


@router.get('/archive', response_model=ApiSuccess[ArchiveListResponse])
async def list_archive(
    _: UserDep,
    service: ArchiveServiceDep,
    fromDate: Annotated[date | None, Query(alias='fromDate')] = None,
    toDate: Annotated[date | None, Query(alias='toDate')] = None,
    status: Annotated[ArchiveStatus | None, Query(alias='status')] = None,
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
