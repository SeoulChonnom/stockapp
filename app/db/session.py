from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.settings import get_settings
from app.db.identifiers import build_search_path_sql


@lru_cache
def get_async_engine() -> AsyncEngine:
    settings = get_settings()
    engine = create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds,
    )

    @event.listens_for(engine.sync_engine, 'connect')
    def _set_search_path(dbapi_connection, _connection_record) -> None:
        # SET search_path runs inside psycopg's implicit transaction, and the
        # pool's reset_on_return='rollback' issues ROLLBACK on first checkin,
        # silently reverting it unless it is committed. Toggling autocommit
        # around the statement commits it immediately instead of relying on
        # a session commit that may never come (e.g. read-only checkouts).
        previous_autocommit = dbapi_connection.autocommit
        dbapi_connection.autocommit = True
        try:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute(build_search_path_sql(settings.database_schema))
            finally:
                cursor.close()
        finally:
            dbapi_connection.autocommit = previous_autocommit

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
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


__all__ = [
    'AsyncEngine',
    'AsyncSession',
    'get_async_engine',
    'get_db_session',
    'get_session_maker',
]
