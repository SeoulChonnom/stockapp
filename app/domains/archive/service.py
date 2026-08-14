from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from datetime import date

from app.core.exceptions import ValidationError
from app.db.repositories.page_snapshot_repo import PageSnapshotRepository
from app.db.repositories.theme_repo import ThemeRepository
from app.domains.archive.assembler import (
    assemble_theme_catalog_response,
    build_archive_list_payload,
)
from app.schemas.page import ThemeNodeResponse

ARCHIVE_STATUSES = frozenset({'READY', 'PARTIAL'})
ARCHIVE_MARKETS = frozenset({'US', 'KR'})
MAX_ARCHIVE_THEMES = 10
MAX_ARCHIVE_QUERY_TOKENS = 10


class ArchiveService:
    def __init__(
        self,
        repository: PageSnapshotRepository,
        theme_repository: ThemeRepository,
    ) -> None:
        self._repo = repository
        self._theme_repo = theme_repository

    async def list_archive(
        self,
        from_date: date | None,
        to_date: date | None,
        status: str | None,
        page: int,
        size: int,
        market_type: str | None = None,
        themes: Sequence[str] | None = None,
        query: str | None = None,
    ) -> dict[str, object]:
        normalized_status = status.upper() if status is not None else None
        if normalized_status is not None and normalized_status not in ARCHIVE_STATUSES:
            raise ValidationError(
                'UNSUPPORTED_ARCHIVE_STATUS', f'Unsupported archive status: {status}'
            )
        normalized_market_type = (
            market_type.upper() if market_type is not None else None
        )
        if (
            normalized_market_type is not None
            and normalized_market_type not in ARCHIVE_MARKETS
        ):
            raise ValidationError(
                'REQUEST_VALIDATION_ERROR',
                f'Unsupported archive market: {market_type}',
                status_code=422,
            )

        normalized_themes = _normalize_theme_codes(themes)
        if len(normalized_themes) > MAX_ARCHIVE_THEMES:
            raise ValidationError(
                'REQUEST_VALIDATION_ERROR',
                'At most 10 archive themes may be selected.',
                status_code=422,
            )
        expanded_themes: list[str] = []
        if normalized_themes:
            invalid_themes = await self._theme_repo.validate_active_theme_codes(
                normalized_themes
            )
            if invalid_themes:
                raise ValidationError(
                    'INVALID_THEME',
                    'One or more archive themes are unknown or inactive.',
                    status_code=422,
                    details={'invalidThemes': invalid_themes},
                )
            expanded_themes = await self._theme_repo.expand_active_theme_codes(
                normalized_themes
            )

        query_tokens = normalize_archive_query(query)
        items = await self._repo.list_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=normalized_status,
            market_type=normalized_market_type,
            theme_codes=expanded_themes,
            query_tokens=query_tokens,
            page=page,
            size=size,
        )
        total_count = await self._repo.count_archive_page_headers(
            from_date=from_date,
            to_date=to_date,
            status=normalized_status,
            market_type=normalized_market_type,
            theme_codes=expanded_themes,
            query_tokens=query_tokens,
        )
        return build_archive_list_payload(
            items,
            page=page,
            size=size,
            total_count=total_count,
        )

    async def list_theme_catalog(self) -> list[ThemeNodeResponse]:
        """Return the validated active archive theme tree."""
        rows = await self._theme_repo.list_active_tree_rows()
        return assemble_theme_catalog_response(rows)


def _normalize_theme_codes(themes: Sequence[str] | None) -> list[str]:
    if themes is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for theme in themes:
        code = theme.strip()
        if not code:
            raise ValidationError(
                'REQUEST_VALIDATION_ERROR',
                'Archive theme codes must not be blank.',
                status_code=422,
            )
        if code not in seen:
            normalized.append(code)
            seen.add(code)
    if len(normalized) > MAX_ARCHIVE_THEMES:
        raise ValidationError(
            'REQUEST_VALIDATION_ERROR',
            'At most 10 distinct archive themes may be selected.',
            status_code=422,
        )
    return normalized


def normalize_archive_query(query: str | None) -> list[str]:
    if query is None:
        return []
    normalized = ' '.join(unicodedata.normalize('NFC', query).casefold().split())
    if not 2 <= len(normalized) <= 100:
        raise ValidationError(
            'REQUEST_VALIDATION_ERROR',
            'Archive query must contain 2 to 100 normalized characters.',
            status_code=422,
        )
    tokens = normalized.split(' ')
    if len(tokens) > MAX_ARCHIVE_QUERY_TOKENS:
        raise ValidationError(
            'REQUEST_VALIDATION_ERROR',
            'Archive query may contain at most 10 normalized tokens.',
            status_code=422,
        )
    return tokens


__all__ = [
    'ARCHIVE_MARKETS',
    'ARCHIVE_STATUSES',
    'MAX_ARCHIVE_QUERY_TOKENS',
    'MAX_ARCHIVE_THEMES',
    'ArchiveService',
    'normalize_archive_query',
]
