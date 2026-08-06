from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response

from app.api.deps import DbSession, UserDep
from app.core.response import ApiSuccess
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.domains.pages.assembler import assemble_daily_page_response
from app.domains.pages.service import PagesService
from app.schemas.page import DailyPageResponse

router = APIRouter(prefix='/pages', tags=['pages'])


def get_pages_service(session: DbSession) -> PagesService:
    return PagesService(PageSnapshotRepository(session))


type PagesServiceDep = Annotated[PagesService, Depends(get_pages_service)]


@router.get('/daily/latest', response_model=ApiSuccess[DailyPageResponse])
async def get_latest_page(
    _: UserDep,
    service: PagesServiceDep,
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_latest_page()
    return ApiSuccess(data=assemble_daily_page_response(payload))


@router.get('/daily', response_model=ApiSuccess[DailyPageResponse])
async def get_page_by_business_date(
    _: UserDep,
    service: PagesServiceDep,
    response: Response,
    businessDate: Annotated[date, Query(alias='businessDate')],
    versionNo: Annotated[int | None, Query(alias='versionNo', ge=1)] = None,
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_page_by_date(businessDate, versionNo)
    data = assemble_daily_page_response(payload)
    _set_historical_cache_headers(response, data)
    return ApiSuccess(data=data)


@router.get('/{pageId}', response_model=ApiSuccess[DailyPageResponse])
async def get_page_by_id(
    _: UserDep,
    service: PagesServiceDep,
    response: Response,
    pageId: Annotated[int, Path(alias='pageId', ge=1)],
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_page_by_id(pageId)
    data = assemble_daily_page_response(payload)
    _set_historical_cache_headers(response, data)
    return ApiSuccess(data=data)


def _set_historical_cache_headers(response: Response, data: DailyPageResponse) -> None:
    if data.status == 'READY' and not data.metadata.isLatest:
        response.headers['Cache-Control'] = 'public, max-age=300, immutable'


__all__ = ['get_pages_service', 'router']
