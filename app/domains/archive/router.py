from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import DbSession, UserDep
from app.core.openapi_responses import (
    AUTH_RESPONSES,
    error_response,
    merge_responses,
)
from app.core.response import ApiSuccess
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.db.repositories.theme_repo import ThemeRepository
from app.domains.archive.assembler import (
    assemble_archive_list_response,
    encode_theme_catalog_success_response,
)
from app.domains.archive.service import ArchiveService
from app.schemas.page import ArchiveListResponse, ThemeNodeResponse

router = APIRouter(prefix='/pages', tags=['archive'])

_LIST_ARCHIVE_RESPONSES = merge_responses(
    AUTH_RESPONSES,
    error_response(
        422,
        'Invalid archive status filter. Codes: REQUEST_VALIDATION_ERROR.',
    ),
)


def get_archive_service(session: DbSession) -> ArchiveService:
    return ArchiveService(PageSnapshotRepository(session), ThemeRepository(session))


type ArchiveServiceDep = Annotated[ArchiveService, Depends(get_archive_service)]
type ArchiveStatus = Literal['READY', 'PARTIAL']


@router.get(
    '/archive/themes',
    response_model=ApiSuccess[list[ThemeNodeResponse]],
    responses=AUTH_RESPONSES,
)
async def list_archive_themes(
    _: UserDep,
    service: ArchiveServiceDep,
) -> Response:
    payload = ApiSuccess[list[ThemeNodeResponse]](
        data=await service.list_theme_catalog()
    )
    return Response(
        content=encode_theme_catalog_success_response(payload),
        media_type='application/json',
    )


@router.get(
    '/archive',
    response_model=ApiSuccess[ArchiveListResponse],
    responses=_LIST_ARCHIVE_RESPONSES,
)
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


__all__ = ['get_archive_service', 'list_archive_themes', 'router']
