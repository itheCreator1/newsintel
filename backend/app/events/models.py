import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.clustering.models import StoryCluster  # noqa: F401
from app.db.base import Base
from app.feeds.models import Article  # noqa: F401
from app.nlp.models import Entity  # noqa: F401

# The imports above register `story_clusters`, `articles` and `nlp_entities` for these tables'
# foreign keys, so the models also resolve in processes (scheduler, worker) that never load the API.

# active: still collecting clusters; closed: none expected; superseded: replaced by a newer
# algorithm version's event and kept for provenance.
STATUSES = ("active", "closed", "superseded")


class Event(Base):
    """A happening covered by one or more story clusters, as one algorithm version sees it.

    `started_at`, `ended_at` and `primary_country` are cached, derived values (from the member
    clusters and the story-role country annotations) so events can be filtered and ordered by
    index; whoever changes the associations recomputes them.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("id", "algorithm_version", name="uq_events_id_version"),
        Index("ix_events_status_time", "status", "ended_at", "id"),
        Index("ix_events_country_time", "primary_country", "ended_at", "id"),
        Index("ix_events_time", "ended_at", "id"),
        Index("ix_events_algorithm", "algorithm_version", "id"),
        CheckConstraint("status IN ('active', 'closed', 'superseded')", name="ck_events_status"),
        CheckConstraint("ended_at >= started_at", name="ck_events_span"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    primary_country: Mapped[str | None] = mapped_column(String(2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EventCluster(Base):
    """A cluster's membership in an event. Articles are reached through the cluster, never copied.

    The version is repeated from the event and pinned to it by the composite key, so "one event per
    cluster per algorithm version" is enforceable; a newer version associates the cluster again.
    """

    __tablename__ = "event_clusters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["event_id", "algorithm_version"],
            ["events.id", "events.algorithm_version"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "algorithm_version", "cluster_id", name="uq_event_clusters_version_cluster"
        ),
        Index("ix_event_clusters_cluster", "cluster_id"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("story_clusters.id", ondelete="CASCADE"), primary_key=True
    )
    algorithm_version: Mapped[str] = mapped_column(String(64))
    score: Mapped[float] = mapped_column(Float)
    # The deterministic signals behind the score, so a decision can be explained without re-running.
    signals: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The cluster's `updated_at` this decision was based on; a newer one makes it dirty again.
    cluster_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EventEntity(Base):
    """An entity characterising an event; `article_count` is its derived distinct-article count."""

    __tablename__ = "event_entities"
    __table_args__ = (
        Index("ix_event_entities_entity", "entity_id", "event_id"),
        CheckConstraint("article_count > 0", name="ck_event_entities_count"),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), primary_key=True
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE"), primary_key=True
    )
    article_count: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventAssociationRun(Base):
    """One association run that did work or failed; an idle scheduler tick writes nothing.

    Rows older than `RUN_RETENTION` (`app.events.execution`) are deleted as new ones are written.
    """

    __tablename__ = "event_association_runs"
    __table_args__ = (Index("ix_event_association_runs_started", "started_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sweep: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    evaluated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    deleted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    failed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
