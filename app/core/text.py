"""Shared text normalization helpers used by persisted search snapshots."""

from __future__ import annotations

import unicodedata


def normalize_text(value: str | None) -> str:
    """NFC-compose, case-fold, and collapse whitespace in source text."""

    if value is None:
        return ''
    if not isinstance(value, str):
        raise TypeError('value must be a string or None')
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


def normalize_search_document(
    page_title: str | None,
    global_headline: str | None,
) -> str:
    """Build the immutable normalized search document for one page snapshot."""

    return ' '.join(
        normalized
        for value in (page_title, global_headline)
        if (normalized := normalize_text(value))
    )


__all__ = ['normalize_search_document', 'normalize_text']
