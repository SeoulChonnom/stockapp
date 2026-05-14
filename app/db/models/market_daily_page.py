from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.enums import MarketType, PageStatus


class MarketDailyPage(Base):
    __tablename__ = 'market_daily_page'

    id: Mapped[int] = mapped_column(primary_key=True)
    business_date: Mapped[date] = mapped_column(Date)
    version_no: Mapped[int] = mapped_column(Integer)
    page_title: Mapped[str] = mapped_column(Text)
    status: Mapped[PageStatus] = mapped_column(
        Enum(PageStatus, name='page_status_enum')
    )
    global_headline: Mapped[str | None] = mapped_column(Text)
    partial_message: Mapped[str | None] = mapped_column(Text)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    raw_news_count: Mapped[int] = mapped_column(Integer)
    processed_news_count: Mapped[int] = mapped_column(Integer)
    cluster_count: Mapped[int] = mapped_column(Integer)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    batch_job_id: Mapped[int] = mapped_column(Integer)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    markets: Mapped[list[MarketDailyPageMarket]] = relationship(
        back_populates='page',
        order_by='MarketDailyPageMarket.display_order',
    )


class MarketDailyPageMarket(Base):
    __tablename__ = 'market_daily_page_market'
    __table_args__ = (
        UniqueConstraint('page_id', 'market_type'),
        UniqueConstraint('page_id', 'display_order'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey('stock.market_daily_page.id', ondelete='CASCADE')
    )
    market_type: Mapped[MarketType] = mapped_column(
        Enum(MarketType, name='market_type_enum')
    )
    display_order: Mapped[int] = mapped_column(SmallInteger)
    market_label: Mapped[str] = mapped_column(Text)
    summary_title: Mapped[str | None] = mapped_column(Text)
    summary_body: Mapped[str | None] = mapped_column(Text)
    analysis_background_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    analysis_key_themes_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    analysis_outlook: Mapped[str | None] = mapped_column(Text)
    raw_news_count: Mapped[int] = mapped_column(Integer)
    processed_news_count: Mapped[int] = mapped_column(Integer)
    cluster_count: Mapped[int] = mapped_column(Integer)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    partial_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    page: Mapped[MarketDailyPage] = relationship(back_populates='markets')
    indices: Mapped[list[MarketDailyPageMarketIndex]] = relationship(
        back_populates='page_market',
        order_by='MarketDailyPageMarketIndex.display_order',
    )
    clusters: Mapped[list[MarketDailyPageMarketCluster]] = relationship(
        back_populates='page_market',
        order_by='MarketDailyPageMarketCluster.display_order',
    )
    article_links: Mapped[list[MarketDailyPageArticleLink]] = relationship(
        back_populates='page_market',
        order_by='MarketDailyPageArticleLink.display_order',
    )


class MarketDailyPageMarketIndex(Base):
    __tablename__ = 'market_daily_page_market_index'

    id: Mapped[int] = mapped_column(primary_key=True)
    page_market_id: Mapped[int] = mapped_column(
        ForeignKey('stock.market_daily_page_market.id', ondelete='CASCADE')
    )
    market_index_daily_id: Mapped[int | None] = mapped_column(Integer)
    display_order: Mapped[int] = mapped_column(SmallInteger)
    index_code: Mapped[str] = mapped_column(Text)
    index_name: Mapped[str] = mapped_column(Text)
    close_price: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    change_value: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    change_percent: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    high_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    low_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency_code: Mapped[str]

    page_market: Mapped[MarketDailyPageMarket] = relationship(back_populates='indices')


class MarketDailyPageMarketCluster(Base):
    __tablename__ = 'market_daily_page_market_cluster'

    id: Mapped[int] = mapped_column(primary_key=True)
    page_market_id: Mapped[int] = mapped_column(
        ForeignKey('stock.market_daily_page_market.id', ondelete='CASCADE')
    )
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    cluster_uid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True))
    display_order: Mapped[int] = mapped_column(SmallInteger)
    title: Mapped[str] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    article_count: Mapped[int] = mapped_column(Integer)
    tags_json: Mapped[list[str]] = mapped_column(JSONB, default=list)
    representative_article_id: Mapped[int | None] = mapped_column(Integer)
    representative_title: Mapped[str | None] = mapped_column(Text)
    representative_publisher_name: Mapped[str | None] = mapped_column(Text)
    representative_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    representative_origin_link: Mapped[str | None] = mapped_column(Text)
    representative_naver_link: Mapped[str | None] = mapped_column(Text)

    page_market: Mapped[MarketDailyPageMarket] = relationship(back_populates='clusters')


class MarketDailyPageArticleLink(Base):
    __tablename__ = 'market_daily_page_article_link'

    id: Mapped[int] = mapped_column(primary_key=True)
    page_market_id: Mapped[int] = mapped_column(
        ForeignKey('stock.market_daily_page_market.id', ondelete='CASCADE')
    )
    display_order: Mapped[int] = mapped_column(Integer)
    processed_article_id: Mapped[int | None] = mapped_column(Integer)
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    cluster_uid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    cluster_title: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    publisher_name: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origin_link: Mapped[str] = mapped_column(Text)
    naver_link: Mapped[str | None] = mapped_column(Text)

    page_market: Mapped[MarketDailyPageMarket] = relationship(
        back_populates='article_links'
    )


__all__ = [
    'MarketDailyPage',
    'MarketDailyPageArticleLink',
    'MarketDailyPageMarket',
    'MarketDailyPageMarketCluster',
    'MarketDailyPageMarketIndex',
]
