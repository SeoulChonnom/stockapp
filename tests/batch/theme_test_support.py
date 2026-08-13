from __future__ import annotations

from types import SimpleNamespace

from app.batch.theme_rules import CANONICAL_LEAF_CODES, CANONICAL_PARENT_CODES


def active_theme_tree_rows(
    *,
    inactive_codes: set[str] | None = None,
) -> list[SimpleNamespace]:
    """Build the complete seeded tree used by strict batch fakes."""

    inactive = inactive_codes or set()
    all_codes = (*sorted(CANONICAL_PARENT_CODES), *CANONICAL_LEAF_CODES)
    return [
        SimpleNamespace(
            code=code,
            parent_code=_nearest_parent(code),
            is_active=code not in inactive,
        )
        for code in all_codes
    ]


def _nearest_parent(code: str) -> str | None:
    candidates = [
        parent
        for parent in CANONICAL_PARENT_CODES
        if parent != code and code.startswith(f'{parent}_')
    ]
    return max(candidates, key=len) if candidates else None


class StrictThemeRepository:
    """ThemeRepository-shaped fake that always exposes the strict contract."""

    def __init__(
        self,
        session: object,
        *,
        rows: list[object] | None = None,
        read_error: BaseException | None = None,
        calls: list[object] | None = None,
        read_calls: list[object] | None = None,
    ) -> None:
        self.session = session
        self.rows = rows if rows is not None else active_theme_tree_rows()
        self.read_error = read_error
        self.calls = calls if calls is not None else []
        self.read_calls = read_calls if read_calls is not None else []
        self.calls.append(session)

    async def list_active_tree_rows(self) -> list[object]:
        self.read_calls.append(self.session)
        if self.read_error is not None:
            raise self.read_error
        return list(self.rows)
