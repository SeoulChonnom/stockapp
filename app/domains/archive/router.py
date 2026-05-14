from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import UserDep
from app.core.response import ApiSuccess
from app.domains.archive.service import ArchiveService, get_archive_service
from app.schemas.page import ArchiveListResponse

router = APIRouter()
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
    return ApiSuccess(data=payload)


__all__ = ['router']
