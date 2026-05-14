from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from typing import Protocol
from urllib.parse import urlparse

import certifi
import httpx
from bs4 import BeautifulSoup

from app.core.settings import Settings, get_settings

_WHITESPACE_RE = re.compile(r'\s+')


class _ArticleHttpResponse(Protocol):
    text: str

    def raise_for_status(self) -> None: ...


class _ArticleHttpClient(Protocol):
    async def get(self, url: str) -> _ArticleHttpResponse: ...


@dataclass(slots=True)
class ArticleContentResult:
    body_text: str | None
    body_excerpt: str | None
    source_summary: str | None
    source_domain: str | None
    fetched_url: str | None
    fallback_used: bool
    failure_details: list[dict[str, str]]


class ArticleContentProvider:
    provider_name = 'ArticleContentProvider'

    def __init__(
        self,
        settings: Settings | None = None,
        client: _ArticleHttpClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.article_crawl_timeout_seconds,
            verify=certifi.where(),
            headers={'User-Agent': self._settings.article_crawl_user_agent},
            follow_redirects=True,
        )

    async def fetch_article_content(
        self,
        *,
        origin_link: str | None,
        naver_link: str | None,
        fallback_summary: str | None,
    ) -> ArticleContentResult:
        if self._client is not None:
            return await self._fetch_with_client(
                self._client,
                origin_link=origin_link,
                naver_link=naver_link,
                fallback_summary=fallback_summary,
            )

        try:
            async with self._build_client() as client:
                return await self._fetch_with_client(
                    client,
                    origin_link=origin_link,
                    naver_link=naver_link,
                    fallback_summary=fallback_summary,
                )
        except Exception as exc:
            return self._fallback_result(
                origin_link=origin_link,
                naver_link=naver_link,
                fallback_summary=fallback_summary,
                failure_details=[
                    self._failure_detail(url, exc)
                    for url in [origin_link, naver_link]
                    if url
                ],
            )

    async def _fetch_with_client(
        self,
        client: _ArticleHttpClient,
        *,
        origin_link: str | None,
        naver_link: str | None,
        fallback_summary: str | None,
    ) -> ArticleContentResult:
        failure_details: list[dict[str, str]] = []
        for url in [origin_link, naver_link]:
            if not url:
                continue
            try:
                response = await client.get(url)
                response.raise_for_status()
                body_text = self._extract_body_text(response.text)
                if body_text:
                    return ArticleContentResult(
                        body_text=body_text,
                        body_excerpt=self._excerpt(body_text),
                        source_summary=fallback_summary,
                        source_domain=urlparse(url).netloc or None,
                        fetched_url=url,
                        fallback_used=False,
                        failure_details=failure_details,
                    )
            except Exception as exc:
                failure_details.append(
                    self._failure_detail(url, exc)
                )
                continue

        return self._fallback_result(
            origin_link=origin_link,
            naver_link=naver_link,
            fallback_summary=fallback_summary,
            failure_details=failure_details,
        )

    def _failure_detail(self, url: str, exc: Exception) -> dict[str, str]:
        return {
            'provider': self.provider_name,
            'url': url,
            'error_class': type(exc).__name__,
            'error_message': str(exc),
        }

    def _fallback_result(
        self,
        *,
        origin_link: str | None,
        naver_link: str | None,
        fallback_summary: str | None,
        failure_details: list[dict[str, str]],
    ) -> ArticleContentResult:
        return ArticleContentResult(
            body_text=fallback_summary,
            body_excerpt=self._excerpt(fallback_summary),
            source_summary=fallback_summary,
            source_domain=urlparse(origin_link or naver_link or '').netloc or None,
            fetched_url=origin_link or naver_link,
            fallback_used=True,
            failure_details=failure_details,
        )

    @staticmethod
    def _extract_body_text(html: str) -> str | None:
        soup = BeautifulSoup(html, 'html.parser')
        selectors = [
            'article',
            'main',
            '#dic_area',
            '.article_body',
            '.article_view',
            '.news_end',
        ]
        text_chunks: list[str] = []
        for selector in selectors:
            node = soup.select_one(selector)
            if node is not None:
                text_chunks = [node.get_text(' ', strip=True)]
                break
        if not text_chunks:
            meta_description = soup.select_one("meta[name='description']")
            if meta_description is not None and meta_description.get('content'):
                text_chunks = [str(meta_description.get('content'))]

        normalized = _WHITESPACE_RE.sub(' ', unescape(' '.join(text_chunks))).strip()
        return normalized or None

    @staticmethod
    def _excerpt(text: str | None, *, max_length: int = 280) -> str | None:
        if not text:
            return None
        normalized = _WHITESPACE_RE.sub(' ', text).strip()
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1].rstrip() + '…'


__all__ = ['ArticleContentProvider', 'ArticleContentResult']
