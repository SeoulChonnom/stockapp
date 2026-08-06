from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


async def clone_page_markets(
    source_markets: list[dict[str, Any]],
    *,
    page_id: int,
    snapshot_repo: Any,
    build_fields: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[int, int]:
    """Create page_market rows cloned from `source_markets`.

    `build_fields` receives each source market row and must return the keyword
    arguments for `snapshot_repo.create_page_market` (excluding `page_id`).
    Returns a mapping from the source market id to the newly created id.
    """
    new_market_ids: dict[int, int] = {}
    for source_market in source_markets:
        fields = build_fields(source_market)
        new_market_ids[source_market['id']] = await snapshot_repo.create_page_market(
            page_id=page_id, **fields
        )
    return new_market_ids


async def clone_child_rows(
    source_rows: list[dict[str, Any]],
    *,
    new_market_ids: dict[int, int],
    insert_fn: Callable[[dict[str, Any]], Awaitable[Any]],
    transform: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> None:
    """Insert rows (indices/clusters/article links) cloned from `source_rows`.

    Each source row is copied verbatim (excluding `id`) and its
    `page_market_id` is remapped via `new_market_ids`. `transform`, if given,
    receives the source row and the built payload and may return an adjusted
    payload before it is passed to `insert_fn`.
    """
    for source_row in source_rows:
        payload = {key: value for key, value in source_row.items() if key != 'id'}
        payload['page_market_id'] = new_market_ids[source_row['page_market_id']]
        if transform is not None:
            payload = transform(source_row, payload)
        await insert_fn(payload)


__all__ = ['clone_child_rows', 'clone_page_markets']
