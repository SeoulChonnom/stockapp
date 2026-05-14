from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.enums import AiSummaryStatus, AiSummaryType, MarketType


class AiSummary(Base):
    __tablename__ = 'ai_summary'

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_job_id: Mapped[int] = mapped_column(Integer)
    summary_type: Mapped[AiSummaryType] = mapped_column(
        Enum(AiSummaryType, name='ai_summary_type_enum')
    )
    business_date: Mapped[date] = mapped_column(Date)
    market_type: Mapped[MarketType | None] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    paragraphs_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    model_name: Mapped[str | None] = mapped_column(Text)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AiSummaryStatus] = mapped_column(
        Enum(AiSummaryStatus, name='ai_summary_status_enum')
    )
    fallback_used: Mapped[bool]
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = ['AiSummary']
