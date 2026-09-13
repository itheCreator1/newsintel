from collections.abc import AsyncIterator
from threading import local

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

_thread_state = local()


def session_factory() -> AsyncSession:
    maker = getattr(_thread_state, "session_maker", None)
    if maker is None:
        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        _thread_state.session_maker = maker
    return maker()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
