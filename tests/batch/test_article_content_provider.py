from __future__ import annotations

import asyncio

import pytest

import app.batch.providers.article_content as article_content_module
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


@pytest.mark.anyio
async def test_article_content_provider_offloads_html_parsing_to_a_thread(
    monkeypatch,
):
    """BeautifulSoup parsing is CPU-bound and must not block the event loop
    while other concurrently-crawled articles are awaiting their turn."""
    to_thread_calls: list[object] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(func, /, *args, **kwargs):
        to_thread_calls.append(func)
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(
        article_content_module.asyncio, 'to_thread', recording_to_thread
    )

    class SingleUrlClient:
        async def get(self, url: str) -> FakeResponse:
            _ = url
            return FakeResponse('<article>Body text</article>')

    provider = ArticleContentProvider(client=SingleUrlClient())

    result = await provider.fetch_article_content(
        origin_link='https://origin.example/news/1',
        naver_link=None,
        fallback_summary=None,
    )

    assert result.body_text == 'Body text'
    assert to_thread_calls == [provider._extract_body_text]
