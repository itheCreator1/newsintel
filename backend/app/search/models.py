import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArticleSearchState(Base):
    __tablename__ = "article_search_states"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    requested_revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchIndexTarget(Base):
    __tablename__ = "search_index_targets"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    index_name: Mapped[str] = mapped_column(String(255), unique=True)
    schema_version: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SearchDelivery(Base):
    __tablename__ = "search_deliveries"
    __table_args__ = (
        UniqueConstraint("article_id", "target_id", name="uq_search_delivery_article_target"),
        Index("ix_search_deliveries_due", "status", "next_attempt_at", "claim_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("search_index_targets.id", ondelete="CASCADE")
    )
    requested_revision: Mapped[int] = mapped_column(Integer)
    indexed_revision: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchRebuild(Base):
    __tablename__ = "search_rebuilds"
    __table_args__ = (Index("uq_search_rebuild_active", "active_key", unique=True),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    target_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("search_index_targets.id", ondelete="RESTRICT")
    )
    active_key: Mapped[str | None] = mapped_column(String(16), default="active")
    status: Mapped[str] = mapped_column(String(24), default="scanning")
    scan_cursor: Mapped[uuid.UUID | None]
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    coordinator_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    coordinator_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cutover_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SourceSearchRefresh(Base):
    __tablename__ = "source_search_refreshes"
    __table_args__ = (Index("ix_source_search_refresh_due", "status", "next_attempt_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id", ondelete="RESTRICT"))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    article_cursor: Mapped[uuid.UUID | None]
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
