from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.settings import get_settings


@lru_cache
def get_async_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_search_path(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute(f"SET search_path TO {settings.database_schema}, public")
        finally:
            cursor.close()

    return engine


@lru_cache
def get_session_maker() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=get_async_engine(),
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )


async def get_db_session() -> AsyncIterator[AsyncSession]:
    session_maker = get_session_maker()
    async with session_maker() as session:
        yield session


__all__ = ["AsyncEngine", "AsyncSession", "get_async_engine", "get_db_session", "get_session_maker"]
