from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import MarketType


class NewsCluster(Base):
    __tablename__ = 'news_cluster'

    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_uid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    business_date: Mapped[date] = mapped_column(Date)
    market_type: Mapped[MarketType] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    cluster_rank: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    summary_short: Mapped[str | None] = mapped_column(Text)
    summary_long: Mapped[str | None] = mapped_column(Text)
    analysis_paragraphs_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    tags_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    representative_article_id: Mapped[int] = mapped_column(Integer)
    article_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    articles: Mapped[list[NewsClusterArticle]] = relationship(
        back_populates='cluster',
        order_by='NewsClusterArticle.article_rank',
    )


class NewsClusterArticle(Base):
    __tablename__ = 'news_cluster_article'

    cluster_id: Mapped[int] = mapped_column(
        ForeignKey('stock.news_cluster.id', ondelete='CASCADE'),
        primary_key=True,
    )
    processed_article_id: Mapped[int] = mapped_column(
        ForeignKey('stock.news_article_processed.id', ondelete='RESTRICT'),
        primary_key=True,
    )
    article_rank: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    cluster: Mapped[NewsCluster] = relationship(back_populates='articles')


__all__ = ['NewsCluster', 'NewsClusterArticle']
