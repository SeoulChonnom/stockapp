from __future__ import annotations

from app.batch.providers.naver_news import NaverNewsProvider


def test_parse_pub_date_returns_none_for_invalid_date_header():
    assert NaverNewsProvider._parse_pub_date('not a valid date') is None
