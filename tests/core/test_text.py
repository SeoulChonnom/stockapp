from __future__ import annotations

from app.core.text import normalize_search_document


def test_normalize_search_document_uses_nfc_casefold_and_collapsed_whitespace() -> None:
    assert normalize_search_document('  Straße  Cafe\u0301  ', 'STRASSE\tRésumé') == (
        'strasse café strasse résumé'
    )
