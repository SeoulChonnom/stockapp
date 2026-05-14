from __future__ import annotations

from typing import Any

from app.schemas.page import (
    ArchiveItemResponse,
    ArchiveListResponse,
    PaginationResponse,
)


def assemble_archive_list_response(payload: dict[str, Any]) -> ArchiveListResponse:
    return ArchiveListResponse.model_validate(payload)


def build_archive_list_payload(
    items: list[dict[str, Any]],
    *,
    page: int,
    size: int,
    total_count: int,
) -> dict[str, Any]:
    return ArchiveListResponse(
        items=[ArchiveItemResponse.model_validate(item) for item in items],
        pagination=PaginationResponse(page=page, size=size, totalCount=total_count),
    ).model_dump(mode='json')


__all__ = ['assemble_archive_list_response', 'build_archive_list_payload']
