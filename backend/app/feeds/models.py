import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.articles.models import Article
from app.core.ids import uuid7
from app.db.base import Base, one_of

# rss reads the feed alone; the full-text modes also fetch each article (with its HTML kept).
FETCHING_MODES = ("rss", "full_text", "full_text_html")


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (
        Index("ix_feeds_due", "enabled", "retired_at", "next_poll_at"),
        CheckConstraint(one_of("fetching_mode", FETCHING_MODES), name="ck_feeds_fetching_mode"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    source_country: Mapped[str | None] = mapped_column(String(2))
    expected_language: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    fetching_mode: Mapped[str] = mapped_column(String(16), default="rss")
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    next_poll_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    fetches: Mapped[list["FeedFetch"]] = relationship(back_populates="feed")
    discoveries: Mapped[list["FeedArticle"]] = relationship(back_populates="feed")


# A fetch in any other status is still owed an outcome.
FETCH_FINISHED = ("success", "failed")
FETCH_STATUSES = ("queued", "running", *FETCH_FINISHED)


class FeedFetch(Base):
    __tablename__ = "feed_fetches"
    __table_args__ = (
        Index("ix_feed_fetches_history", "feed_id", "started_at"),
        Index("ix_feed_fetches_started", "started_at"),
        CheckConstraint(one_of("status", FETCH_STATUSES), name="ck_feed_fetches_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id", ondelete="RESTRICT"))
    claim_token: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    entry_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_entry_count: Mapped[int] = mapped_column(Integer, default=0)
    new_article_count: Mapped[int] = mapped_column(Integer, default=0)
    etag: Mapped[str | None] = mapped_column(Text)
    last_modified: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feed: Mapped[Feed] = relationship(back_populates="fetches")


class FeedArticle(Base):
    __tablename__ = "feed_articles"
    __table_args__ = (
        UniqueConstraint("feed_id", "article_id", name="uq_feed_articles_feed_article"),
        UniqueConstraint("feed_id", "guid", name="uq_feed_articles_feed_guid"),
        Index("ix_feed_articles_article", "article_id", "discovered_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id", ondelete="RESTRICT"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))
    guid: Mapped[str | None] = mapped_column(Text)
    feed_title: Mapped[str] = mapped_column(Text)
    feed_url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    feed: Mapped[Feed] = relationship(back_populates="discoveries")
    article: Mapped[Article] = relationship(back_populates="discoveries")
