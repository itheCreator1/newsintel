import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StoryCluster(Base):
    """A story covered by at least two independently published articles."""

    __tablename__ = "story_clusters"
    __table_args__ = (Index("ix_story_clusters_recency", "last_published_at", "id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    article_count: Mapped[int] = mapped_column(Integer, default=0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    representative_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class StoryClusterMember(Base):
    """An article belongs to at most one story cluster, so the article is the key."""

    __tablename__ = "story_cluster_members"
    __table_args__ = (Index("ix_story_cluster_members_cluster", "cluster_id", "article_id"),)
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("story_clusters.id", ondelete="CASCADE")
    )
    score: Mapped[float] = mapped_column(Float)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArticleClusterState(Base):
    __tablename__ = "article_cluster_state"
    __table_args__ = (UniqueConstraint("article_id", name="uq_article_cluster_state"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    requested_generation: Mapped[int] = mapped_column(Integer, default=1)
    completed_generation: Mapped[int] = mapped_column(Integer, default=0)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ClusterJob(Base):
    __tablename__ = "cluster_jobs"
    __table_args__ = (
        UniqueConstraint("article_id", "generation", name="uq_cluster_job_generation"),
        Index("ix_cluster_jobs_due", "status", "next_attempt_at", "claim_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_cluster_state.id", ondelete="CASCADE")
    )
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    generation: Mapped[int] = mapped_column(Integer)
    algorithm_version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
