"""Shared public contract for missing cluster theme diagnostics."""

from __future__ import annotations

from typing import Final

THEME_CLASSIFICATION: Final = 'THEME_CLASSIFICATION'
THEME_CLASSIFICATION_MISSING: Final = 'THEME_CLASSIFICATION_MISSING'
THEME_CLASSIFICATION_MISSING_MESSAGE: Final = (
    '일부 뉴스 주제의 검색 테마를 분류하지 못했습니다.'
)

THEME_CLASSIFICATION_MISSING_ISSUE: Final[dict[str, str]] = {
    'category': THEME_CLASSIFICATION,
    'code': THEME_CLASSIFICATION_MISSING,
    'message': THEME_CLASSIFICATION_MISSING_MESSAGE,
}

_THEME_CLASSIFICATION_MISSING_ALIASES: Final = frozenset(
    {
        THEME_CLASSIFICATION_MISSING,
        THEME_CLASSIFICATION_MISSING_MESSAGE,
    }
)


def theme_classification_missing_issue(reason: object) -> dict[str, str] | None:
    """Return the canonical issue for either accepted internal reason form."""
    if (
        not isinstance(reason, str)
        or reason not in _THEME_CLASSIFICATION_MISSING_ALIASES
    ):
        return None
    return dict(THEME_CLASSIFICATION_MISSING_ISSUE)


__all__ = [
    'THEME_CLASSIFICATION',
    'THEME_CLASSIFICATION_MISSING',
    'THEME_CLASSIFICATION_MISSING_ISSUE',
    'THEME_CLASSIFICATION_MISSING_MESSAGE',
    'theme_classification_missing_issue',
]
