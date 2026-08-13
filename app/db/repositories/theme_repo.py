from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.identifiers import qualify_db_identifier
from app.db.repositories.base import PostgresRepository
from app.db.repositories.projections import ThemeCatalogRecord


class ThemeRepository(PostgresRepository):
    """Read and validate the active hierarchical theme catalog."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def list_active_tree_rows(self) -> list[ThemeCatalogRecord]:
        """Return active catalog rows in deterministic tree order."""
        statement = text(
            """
            SELECT
                code,
                parent_code,
                label,
                description,
                sort_order,
                is_active
            FROM {theme_catalog_table}
            WHERE is_active = TRUE
            ORDER BY parent_code NULLS FIRST, sort_order ASC, code ASC
            """.format(theme_catalog_table=qualify_db_identifier('theme_catalog'))
        )
        result = await self.session.execute(statement)
        return self._models_from_mappings(
            ThemeCatalogRecord,
            [self._row_to_dict(row) for row in result.all()],
        )

    async def expand_active_theme_codes(
        self,
        theme_codes: Sequence[str],
    ) -> list[str]:
        """Expand active inputs to themselves and all active descendants.

        The recursive path guard makes the query terminate even if a catalog
        corruption introduces a cycle.  Inactive nodes are excluded at both
        the seed and recursive steps, so their descendants are not traversed.
        """
        requested_codes = tuple(theme_codes)
        if not requested_codes:
            return []

        statement = text(
            """
            WITH RECURSIVE active_tree AS (
                SELECT
                    theme.code,
                    ARRAY[theme.code]::TEXT[] AS path
                FROM {theme_catalog_table} AS theme
                WHERE theme.is_active = TRUE
                  AND theme.code IN :theme_codes

                UNION ALL

                SELECT
                    child.code,
                    active_tree.path || child.code
                FROM active_tree
                JOIN {theme_catalog_table} AS child
                  ON child.parent_code = active_tree.code
                WHERE child.is_active = TRUE
                  AND NOT (child.code = ANY(active_tree.path))
            )
            SELECT DISTINCT code
            FROM active_tree
            ORDER BY code ASC
            """.format(theme_catalog_table=qualify_db_identifier('theme_catalog'))
        ).bindparams(bindparam('theme_codes', expanding=True))
        result = await self.session.execute(
            statement,
            {'theme_codes': requested_codes},
        )
        return [self._row_to_dict(row)['code'] for row in result.all()]

    async def validate_active_theme_codes(
        self,
        theme_codes: Sequence[str],
    ) -> list[str]:
        """Return unknown or inactive inputs, preserving input order."""
        requested_codes = tuple(theme_codes)
        if not requested_codes:
            return []

        statement = text(
            """
            SELECT code
            FROM {theme_catalog_table}
            WHERE is_active = TRUE
              AND code IN :theme_codes
            """.format(theme_catalog_table=qualify_db_identifier('theme_catalog'))
        ).bindparams(bindparam('theme_codes', expanding=True))
        result = await self.session.execute(
            statement,
            {'theme_codes': requested_codes},
        )
        active_codes = {self._row_to_dict(row)['code'] for row in result.all()}
        return [code for code in requested_codes if code not in active_codes]

    async def validate_active_leaf_theme_codes(
        self,
        theme_codes: Sequence[str],
    ) -> list[str]:
        """Return unknown, inactive, or parent-code inputs in input order."""
        requested_codes = tuple(theme_codes)
        if not requested_codes:
            return []

        statement = text(
            """
            SELECT theme.code
            FROM {theme_catalog_table} AS theme
            WHERE theme.is_active = TRUE
              AND theme.code IN :theme_codes
              AND NOT EXISTS (
                  SELECT 1
                  FROM {theme_catalog_table} AS child
                  WHERE child.parent_code = theme.code
              )
            """.format(theme_catalog_table=qualify_db_identifier('theme_catalog'))
        ).bindparams(bindparam('theme_codes', expanding=True))
        result = await self.session.execute(
            statement,
            {'theme_codes': requested_codes},
        )
        active_leaf_codes = {self._row_to_dict(row)['code'] for row in result.all()}
        return [code for code in requested_codes if code not in active_leaf_codes]


__all__ = ['ThemeRepository']
