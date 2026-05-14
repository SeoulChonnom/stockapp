from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import UserDep
from app.core.exceptions import NotFoundError
from app.core.response import ApiSuccess
from app.domains.pages.service import PagesService, get_pages_service
from app.schemas.page import DailyPageResponse

router = APIRouter()
PagesServiceDep = Annotated[PagesService, Depends(get_pages_service)]


@router.get('/daily/latest', response_model=ApiSuccess[DailyPageResponse])
async def get_latest_page(
    _: UserDep,
    service: PagesServiceDep,
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_latest_page()
    if payload is None:
        raise NotFoundError(
            'LATEST_PAGE_NOT_FOUND', '가장 최근 생성된 페이지가 존재하지 않습니다.'
        )
    return ApiSuccess(data=payload)


@router.get('/daily', response_model=ApiSuccess[DailyPageResponse])
async def get_page_by_business_date(
    _: UserDep,
    service: PagesServiceDep,
    businessDate: Annotated[date, Query(alias='businessDate')],
    versionNo: Annotated[int | None, Query(alias='versionNo', ge=1)] = None,
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_page_by_date(businessDate, versionNo)
    if payload is None:
        raise NotFoundError(
            'PAGE_NOT_FOUND', '요청한 날짜의 페이지가 존재하지 않습니다.'
        )
    return ApiSuccess(data=payload)


@router.get('/{pageId}', response_model=ApiSuccess[DailyPageResponse])
async def get_page_by_id(
    _: UserDep,
    service: PagesServiceDep,
    pageId: Annotated[int, Path(alias='pageId', ge=1)],
) -> ApiSuccess[DailyPageResponse]:
    payload = await service.get_page_by_id(pageId)
    if payload is None:
        raise NotFoundError('PAGE_NOT_FOUND', '요청한 페이지를 찾을 수 없습니다.')
    return ApiSuccess(data=payload)


__all__ = ['router']
