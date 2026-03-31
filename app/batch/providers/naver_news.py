from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html import unescape
import re

import certifi
import httpx

from app.core.settings import Settings, get_settings
from app.core.timezone import KST
from app.db.repositories.projections import NewsArticleRawCreateParams, NewsSearchKeywordRecord

NAVER_NEWS_PROVIDER_NAME = "NAVER_NEWS"
_NAVER_MAX_START = 1000
_NAVER_PAGE_SIZE = 100
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(slots=True)
class NaverCollectedKeywordResult:
    fetched_count: int
    candidate_count: int
    articles: list[NewsArticleRawCreateParams]


class NaverNewsProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def is_configured(self) -> bool:
        return bool(self._settings.naver_client_id and self._settings.naver_client_secret)

    async def collect_for_keyword(
        self,
        *,
        keyword_record: NewsSearchKeywordRecord,
        business_date: date,
    ) -> NaverCollectedKeywordResult:
        if not self.is_configured():
            raise RuntimeError("Naver news API credentials are not configured.")

        fetched_count = 0
        candidate_count = 0
        articles: list[NewsArticleRawCreateParams] = []

        async with self._build_client() as client:
            start = 1
            while start <= _NAVER_MAX_START:
                payload = await self._fetch_page(client=client, query=keyword_record.keyword, start=start)
                items = payload.get("items", [])
                if not items:
                    break

                fetched_count += len(items)
                page_articles, should_stop = self._extract_business_date_articles(
                    items=items,
                    keyword_record=keyword_record,
                    business_date=business_date,
                )
                candidate_count += len(page_articles)
                articles.extend(page_articles)

                if should_stop or len(items) < _NAVER_PAGE_SIZE:
                    break
                start += _NAVER_PAGE_SIZE

        return NaverCollectedKeywordResult(
            fetched_count=fetched_count,
            candidate_count=candidate_count,
            articles=articles,
        )

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._settings.naver_news_timeout_seconds,
            verify=certifi.where(),
        )

    async def _fetch_page(
        self,
        *,
        client: httpx.AsyncClient,
        query: str,
        start: int,
    ) -> dict:
        response = await client.get(
            self._settings.naver_news_base_url,
            params={
                "query": query,
                "display": _NAVER_PAGE_SIZE,
                "start": start,
                "sort": "date",
            },
            headers={
                "X-Naver-Client-Id": self._settings.naver_client_id or "",
                "X-Naver-Client-Secret": self._settings.naver_client_secret or "",
            },
        )
        response.raise_for_status()
        return response.json()

    def _extract_business_date_articles(
        self,
        *,
        items: list[dict],
        keyword_record: NewsSearchKeywordRecord,
        business_date: date,
    ) -> tuple[list[NewsArticleRawCreateParams], bool]:
        matched_articles: list[NewsArticleRawCreateParams] = []
        should_stop = False

        for item in items:
            published_at = self._parse_pub_date(item.get("pubDate"))
            if published_at is None:
                continue

            published_date = published_at.astimezone(KST).date()
            if published_date < business_date:
                should_stop = True
                break
            if published_date != business_date:
                continue

            matched_articles.append(
                NewsArticleRawCreateParams(
                    provider_name=keyword_record.provider_name,
                    provider_article_key=self._build_provider_article_key(item, published_at),
                    market_type=keyword_record.market_type,
                    business_date=business_date,
                    search_keyword=keyword_record.keyword,
                    title=self._clean_html(item.get("title")),
                    publisher_name=None,
                    published_at=published_at,
                    origin_link=item.get("originallink"),
                    naver_link=item.get("link"),
                    payload_json=item,
                )
            )

        return matched_articles, should_stop

    @staticmethod
    def _build_provider_article_key(item: dict, published_at: datetime) -> str:
        origin_link = item.get("originallink") or ""
        naver_link = item.get("link") or ""
        title = item.get("title") or ""
        fingerprint = "|".join(
            [
                origin_link.strip(),
                naver_link.strip(),
                title.strip(),
                published_at.isoformat(),
            ]
        )
        return sha256(fingerprint.encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_html(value: str | None) -> str:
        if not value:
            return ""
        return unescape(_HTML_TAG_RE.sub("", value)).strip()

    @staticmethod
    def _parse_pub_date(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed


__all__ = ["NAVER_NEWS_PROVIDER_NAME", "NaverCollectedKeywordResult", "NaverNewsProvider"]
