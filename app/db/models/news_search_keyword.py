from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import MarketType


class NewsSearchKeyword(Base):
    __tablename__ = 'news_search_keyword'

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_name: Mapped[str] = mapped_column(Text)
    market_type: Mapped[MarketType] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    keyword: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ['NewsSearchKeyword']
