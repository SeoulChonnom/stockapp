from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, Numeric, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import MarketType


class MarketIndexDaily(Base):
    """Market index observation tied to an expected completed session."""

    __tablename__ = 'market_index_daily'
    __table_args__ = (UniqueConstraint('business_date', 'market_type', 'index_code'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_date: Mapped[date] = mapped_column(Date)
    market_type: Mapped[MarketType] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    source_date: Mapped[date | None] = mapped_column(Date)
    expected_session_date: Mapped[date | None] = mapped_column(Date)
    session_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    index_code: Mapped[str] = mapped_column(Text)
    index_name: Mapped[str] = mapped_column(Text)
    close_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    change_value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    change_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency_code: Mapped[str] = mapped_column(Text)
    provider_name: Mapped[str] = mapped_column(Text)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ['MarketIndexDaily']
