from collections.abc import AsyncIterator
from threading import local

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings

_thread_state = local()
# Workers and the CLI run each job in a fresh event loop (asyncio.run), and a pooled asyncpg
# connection cannot outlive its loop, so session_factory stays unpooled. The API serves every
# request from one long-lived loop, so its lifespan opens a pool that requests share.
_request_engine: AsyncEngine | None = None
_request_maker: async_sessionmaker[AsyncSession] | None = None


def session_factory() -> AsyncSession:
    maker = getattr(_thread_state, "session_maker", None)
    if maker is None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        _thread_state.session_maker = maker
    return maker()


def open_request_pool(settings: Settings) -> None:
    global _request_engine, _request_maker
    _request_engine = create_async_engine(
        settings.database_url,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_pool_overflow,
        pool_pre_ping=True,
    )
    _request_maker = async_sessionmaker(_request_engine, expire_on_commit=False)


async def close_request_pool() -> None:
    global _request_engine, _request_maker
    engine, _request_engine, _request_maker = _request_engine, None, None
    if engine is not None:
        await engine.dispose()


async def get_db() -> AsyncIterator[AsyncSession]:
    maker = _request_maker
    async with maker() if maker is not None else session_factory() as session:
        yield session
