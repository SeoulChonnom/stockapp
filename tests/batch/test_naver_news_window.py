from __future__ import annotations

from datetime import UTC, date, datetime
from email.utils import format_datetime

import pytest

from app.batch.providers import naver_news as naver_module
from app.batch.providers.naver_news import NaverNewsProvider
from app.core.settings import Settings
from app.db.repositories.projections import NewsSearchKeywordRecord


def _keyword() -> NewsSearchKeywordRecord:
    created_at = datetime(2026, 7, 28, tzinfo=UTC)
    return NewsSearchKeywordRecord(
        keyword_id=1,
        provider_name='NAVER_NEWS',
        market_type='US',
        keyword='미국 증시',
        is_active=True,
        priority=1,
        created_at=created_at,
        updated_at=created_at,
    )


def _item(published_at: datetime, suffix: str) -> dict:
    return {
        'title': f'Article {suffix}',
        'originallink': f'https://example.com/{suffix}',
        'link': f'https://search.naver.com/{suffix}',
        'pubDate': format_datetime(published_at),
    }


def test_naver_window_is_start_inclusive_and_end_exclusive():
    provider = NaverNewsProvider()
    start_at = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
    end_at = datetime(2026, 7, 28, 22, 0, tzinfo=UTC)

    articles, should_stop = provider._extract_window_articles(
        items=[
            _item(end_at, 'at-end'),
            _item(end_at.replace(hour=21), 'inside'),
            _item(start_at, 'at-start'),
            _item(start_at.replace(hour=21), 'before-start'),
        ],
        keyword_record=_keyword(),
        business_date=date(2026, 7, 29),
        window_start_at=start_at,
        window_end_at=end_at,
    )

    assert [article.title for article in articles] == [
        'Article inside',
        'Article at-start',
    ]
    assert should_stop is True
    assert {article.business_date for article in articles} == {date(2026, 7, 29)}


def test_provider_article_key_uses_canonical_link_before_highlighted_title():
    published_at = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)
    first_key = NaverNewsProvider._build_provider_article_key(
        {
            'title': '<b>미국 증시</b> 상승',
            'originallink': 'HTTPS://EXAMPLE.COM/news/1/',
        },
        published_at,
    )
    second_key = NaverNewsProvider._build_provider_article_key(
        {
            'title': '미국 <b>증시 상승</b>',
            'originallink': 'https://example.com/news/1',
        },
        published_at,
    )

    assert first_key == second_key


def test_provider_article_key_fallback_cleans_highlighted_title():
    published_at = datetime(2026, 7, 28, 20, 0, tzinfo=UTC)
    first_key = NaverNewsProvider._build_provider_article_key(
        {'title': '<b>미국 증시</b> &amp; 환율'},
        published_at,
    )
    second_key = NaverNewsProvider._build_provider_article_key(
        {'title': '미국 증시 & 환율'},
        published_at,
    )

    assert first_key == second_key


@pytest.mark.anyio
async def test_naver_pagination_cap_marks_coverage_incomplete(monkeypatch):
    monkeypatch.setattr(naver_module, '_NAVER_PAGE_SIZE', 2)
    monkeypatch.setattr(naver_module, '_NAVER_MAX_START', 1)
    provider = NaverNewsProvider(
        Settings(naver_client_id='id', naver_client_secret='secret')
    )

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            _ = (exc_type, exc, traceback)
            return False

    async def fake_fetch_page(**_kwargs):
        return {
            'items': [
                _item(datetime(2026, 7, 28, 21, 0, tzinfo=UTC), 'one'),
                _item(datetime(2026, 7, 28, 20, 0, tzinfo=UTC), 'two'),
            ]
        }

    monkeypatch.setattr(provider, '_build_client', FakeClient)
    monkeypatch.setattr(provider, '_fetch_page', fake_fetch_page)

    result = await provider.collect_for_keyword(
        keyword_record=_keyword(),
        business_date=date(2026, 7, 29),
        window_start_at=datetime(2026, 7, 27, 22, 0, tzinfo=UTC),
        window_end_at=datetime(2026, 7, 28, 22, 0, tzinfo=UTC),
    )

    assert result.candidate_count == 2
    assert result.coverage_complete is False


def test_naver_articles_carry_a_publisher_derived_from_the_article_url():
    """Naver returns no publisher field, so it must come from the URL.

    Every one of the 58,608 processed articles collected since 2026-08-12
    stored publisher_name as NULL because this provider hard-coded None,
    leaving readers with no attribution at all. Section subdomains collapse
    to one outlet so biz. and news. hosts do not read as two publishers.
    """
    provider = NaverNewsProvider()
    start_at = datetime(2026, 7, 27, 22, 0, tzinfo=UTC)
    end_at = datetime(2026, 7, 28, 22, 0, tzinfo=UTC)
    published_at = datetime(2026, 7, 28, 1, 0, tzinfo=UTC)

    def item(origin_link: str | None, link: str) -> dict:
        payload = {
            'title': 'Article',
            'link': link,
            'pubDate': format_datetime(published_at),
        }
        if origin_link is not None:
            payload['originallink'] = origin_link
        return payload

    articles, _ = provider._extract_window_articles(
        items=[
            item('https://www.yna.co.kr/view/AKR1', 'https://n.example/1'),
            item('https://biz.sbs.co.kr/article/2', 'https://n.example/2'),
            item('https://www.topstarnews.net/news/3', 'https://n.example/3'),
            # No originallink: the fallback link still identifies the outlet.
            item(None, 'https://www.news1.kr/articles/4'),
        ],
        keyword_record=_keyword(),
        business_date=date(2026, 7, 29),
        window_start_at=start_at,
        window_end_at=end_at,
    )

    assert [article.publisher_name for article in articles] == [
        'yna.co.kr',
        'sbs.co.kr',
        'topstarnews.net',
        'news1.kr',
    ]
