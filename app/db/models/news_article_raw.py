from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import MarketType


class NewsArticleRaw(Base):
    __tablename__ = "news_article_raw"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_name: Mapped[str] = mapped_column(Text)
    provider_article_key: Mapped[str] = mapped_column(Text)
    market_type: Mapped[MarketType] = mapped_column(Enum(MarketType, name="market_type_enum"))
    business_date: Mapped[date] = mapped_column(Date)
    search_keyword: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_link: Mapped[str | None] = mapped_column(Text)
    naver_link: Mapped[str | None] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ["NewsArticleRaw"]
