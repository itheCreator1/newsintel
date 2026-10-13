import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Column, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.auth.models import LoginFailure, Session, User  # noqa: F401
from app.clustering.models import (  # noqa: F401
    ArticleClusterState,
    ClusterJob,
    StoryCluster,
    StoryClusterMember,
)
from app.core.config import get_settings
from app.db.base import Base
from app.events.models import Event, EventCluster, EventEntity  # noqa: F401
from app.feeds.models import (  # noqa: F401
    Article,
    ArticleContent,
    ArticleProcessingAttempt,
    ArticleProcessingJob,
    Feed,
    FeedArticle,
    FeedFetch,
)
from app.investigations.models import SavedSearch  # noqa: F401
from app.marks.models import ArticleMark  # noqa: F401
from app.monitors.models import Monitor  # noqa: F401
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
from app.notifications.models import NotificationSettings  # noqa: F401
from app.operations.models import MaintenanceRun  # noqa: F401
from app.search.models import (  # noqa: F401
    ArticleSearchState,
    SearchDelivery,
    SearchIndexTarget,
    SearchRebuild,
    SourceSearchRefresh,
)
from app.wikidata.models import (  # noqa: F401
    EntityExternalId,
    WikidataCandidate,
    WikidataClass,
    WikidataItem,
    WikidataRequestCount,
    WikidataRun,
    WikidataSearch,
    WikidataThrottle,
)

config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def compare_server_default(
    context: object,
    inspected_column: object,
    metadata_column: Column[object],
    inspected_default: str | None,
    metadata_default: object,
    rendered_metadata_default: str | None,
) -> bool | None:
    """Compare server defaults as alembic does, but for one known difference (below)."""
    # The single-row settings table got a sequence default from its old migration although its
    # one id is always written; the model, which writes it, has a Python default. Β4 drops the
    # sequence (with wikidata_throttle's, which alembic already skips as the autoincrement key).
    if metadata_column.table.name == "notification_settings" and metadata_column.name == "id":
        return False
    return None


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url").replace("+asyncpg", ""),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_server_default=compare_server_default,
    )
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
