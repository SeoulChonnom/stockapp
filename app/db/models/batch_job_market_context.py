from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Enum, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import MarketType


class BatchJobMarketContext(Base):
    """Per-market session and news coverage contract for a batch job."""

    __tablename__ = 'batch_job_market_context'
    __table_args__ = (
        UniqueConstraint(
            'batch_job_id',
            'market_type',
            name='uq_batch_job_market_context_job_market',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_job_id: Mapped[int] = mapped_column(
        ForeignKey('stock.batch_job.id', ondelete='CASCADE')
    )
    market_type: Mapped[MarketType] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    expected_session_date: Mapped[date] = mapped_column(Date)
    actual_index_source_date: Mapped[date | None] = mapped_column(Date)
    session_close_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    news_window_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    news_window_end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    news_coverage_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ['BatchJobMarketContext']
