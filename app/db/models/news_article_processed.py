from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import MarketType


class NewsArticleProcessed(Base):
    __tablename__ = "news_article_processed"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_date: Mapped[date] = mapped_column(Date)
    market_type: Mapped[MarketType] = mapped_column(Enum(MarketType, name="market_type_enum"))
    dedupe_hash: Mapped[str]
    canonical_title: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_link: Mapped[str] = mapped_column(Text)
    naver_link: Mapped[str | None] = mapped_column(Text)
    source_summary: Mapped[str | None] = mapped_column(Text)
    article_body_excerpt: Mapped[str | None] = mapped_column(Text)
    content_json: Mapped[dict] = mapped_column(JSONB, default=dict)


__all__ = ["NewsArticleProcessed"]
