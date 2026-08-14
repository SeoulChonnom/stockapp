from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

from app.db.repositories.projections import ThemeCatalogRecord
from app.schemas.page import (
    ArchiveItemResponse,
    ArchiveListResponse,
    PaginationResponse,
    ThemeNodeResponse,
)


class ThemeCatalogError(RuntimeError):
    """Raised when active theme rows cannot form one valid catalog tree."""


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


def assemble_theme_catalog_response(
    rows: Sequence[ThemeCatalogRecord],
) -> list[ThemeNodeResponse]:
    """Build an ordered theme tree without recursion.

    ``ThemeRepository.list_active_tree_rows`` returns active rows only. The
    parent map is validated before model nodes are connected so a malformed
    catalog can never result in a partially assembled response.
    """
    records_by_code: dict[str, ThemeCatalogRecord] = {}
    for row in rows:
        if not row.is_active:
            continue
        if row.code in records_by_code:
            raise ThemeCatalogError(f'duplicate active theme code: {row.code}')
        records_by_code[row.code] = row

    children_by_parent: dict[str | None, list[str]] = defaultdict(list)
    parent_by_code: dict[str, str | None] = {}
    for code, row in records_by_code.items():
        parent_code = row.parent_code
        parent_by_code[code] = parent_code
        if parent_code is not None and parent_code not in records_by_code:
            raise ThemeCatalogError(
                f'orphan active theme {code}: parent {parent_code} is missing'
            )
        children_by_parent[parent_code].append(code)

    _validate_parent_cycles(parent_by_code)

    def sort_key(code: str) -> tuple[int, str]:
        return records_by_code[code].sort_order, code

    for sibling_codes in children_by_parent.values():
        sibling_codes.sort(key=sort_key)

    nodes = {
        code: ThemeNodeResponse(
            code=record.code,
            label=record.label,
            description=record.description,
            children=[],
        )
        for code, record in records_by_code.items()
    }
    for parent_code, child_codes in children_by_parent.items():
        if parent_code is not None:
            nodes[parent_code].children = [
                nodes[child_code] for child_code in child_codes
            ]

    return [nodes[code] for code in children_by_parent.get(None, [])]


def _validate_parent_cycles(parent_by_code: dict[str, str | None]) -> None:
    state: dict[str, int] = {}
    for starting_code in parent_by_code:
        if state.get(starting_code, 0) == 2:
            continue

        path: list[str] = []
        current_code: str | None = starting_code
        while current_code is not None and state.get(current_code, 0) == 0:
            state[current_code] = 1
            path.append(current_code)
            current_code = parent_by_code[current_code]

        if current_code is not None and state.get(current_code) == 1:
            raise ThemeCatalogError(
                f'cycle detected in active theme catalog at {current_code}'
            )

        for code in path:
            state[code] = 2


__all__ = [
    'ThemeCatalogError',
    'assemble_archive_list_response',
    'assemble_theme_catalog_response',
    'build_archive_list_payload',
]
