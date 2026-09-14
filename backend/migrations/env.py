import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.auth.models import Session, User  # noqa: F401
from app.core.config import get_settings
from app.db.base import Base
from app.feeds.models import (  # noqa: F401
    Article,
    ArticleContent,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
    Feed,
    FeedArticle,
    FeedFetch,
)
from app.nlp.models import (  # noqa: F401
    ArticleCountryAnnotation,
    ArticleEntity,
    ArticleKeyword,
    ArticleLanguageAnnotation,
    ArticleNlpState,
    Entity,
    Keyword,
    NlpJob,
    NlpProcessorRun,
    NlpReprocessingRun,
    StopWordRevision,
)
from app.search.models import (  # noqa: F401
    ArticleSearchState,
    SearchDelivery,
    SearchIndexTarget,
    SearchRebuild,
    SourceSearchRefresh,
)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url").replace("+asyncpg", ""),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


run_migrations_offline() if context.is_offline_mode() else run_migrations_online()
