from __future__ import annotations

import pytest

from app.batch.providers.article_content import ArticleContentProvider


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


class InjectedClient:
    def __init__(self) -> None:
        self.requested_urls: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.requested_urls.append(url)
        if len(self.requested_urls) == 1:
            raise TimeoutError('origin timed out')
        return FakeResponse('<article>Fetched body from naver fallback</article>')


@pytest.mark.anyio
async def test_article_content_provider_reuses_injected_client_for_fallback():
    client = InjectedClient()
    provider = ArticleContentProvider(client=client)

    result = await provider.fetch_article_content(
        origin_link='https://origin.example/news/1',
        naver_link='https://naver.example/news/1',
        fallback_summary='provider fallback summary',
    )

    assert client.requested_urls == [
        'https://origin.example/news/1',
        'https://naver.example/news/1',
    ]
    assert result.body_text == 'Fetched body from naver fallback'
    assert result.body_excerpt == 'Fetched body from naver fallback'
    assert result.source_summary == 'provider fallback summary'
    assert result.source_domain == 'naver.example'
    assert result.fetched_url == 'https://naver.example/news/1'
    assert result.fallback_used is False
    assert result.failure_details == [
        {
            'provider': 'ArticleContentProvider',
            'url': 'https://origin.example/news/1',
            'error_class': 'TimeoutError',
            'error_message': 'origin timed out',
        }
    ]
