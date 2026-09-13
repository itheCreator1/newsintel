import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Feed(Base):
    __tablename__ = "feeds"
    __table_args__ = (Index("ix_feeds_due", "enabled", "retired_at", "next_poll_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    source_country: Mapped[str | None] = mapped_column(String(2))
    expected_language: Mapped[str | None] = mapped_column(String(16))
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    poll_interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
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


class FeedFetch(Base):
    __tablename__ = "feed_fetches"
    __table_args__ = (Index("ix_feed_fetches_history", "feed_id", "started_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
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


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_articles_normalized_url"),
        Index("ix_articles_discovery", "first_discovered_at", "id"),
        Index("ix_articles_title_hash", "normalized_title_hash"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    original_url: Mapped[str] = mapped_column(Text)
    normalized_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    normalized_title_hash: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    discoveries: Mapped[list["FeedArticle"]] = relationship(back_populates="article")
    content: Mapped["ArticleContent | None"] = relationship(back_populates="article")
    processing_jobs: Mapped[list["ArticleProcessingJob"]] = relationship(back_populates="article")


class FeedArticle(Base):
    __tablename__ = "feed_articles"
    __table_args__ = (
        UniqueConstraint("feed_id", "article_id", name="uq_feed_articles_feed_article"),
        UniqueConstraint("feed_id", "guid", name="uq_feed_articles_feed_guid"),
        Index("ix_feed_articles_article", "article_id", "discovered_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    feed_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("feeds.id", ondelete="RESTRICT"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="RESTRICT"))
    guid: Mapped[str | None] = mapped_column(Text)
    feed_title: Mapped[str] = mapped_column(Text)
    feed_url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    feed: Mapped[Feed] = relationship(back_populates="discoveries")
    article: Mapped[Article] = relationship(back_populates="discoveries")


class ArticleContent(Base):
    __tablename__ = "article_contents"
    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    previous_content_hash: Mapped[str | None] = mapped_column(String(64))
    change_count: Mapped[int] = mapped_column(Integer, default=0)
    extractor_name: Mapped[str] = mapped_column(String(64))
    extractor_version: Mapped[str] = mapped_column(String(32))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_content_change_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    html_object_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    article: Mapped[Article] = relationship(back_populates="content")


class ArticleProcessingJob(Base):
    __tablename__ = "article_processing_jobs"
    __table_args__ = (
        Index("ix_article_jobs_due", "status", "next_attempt_at", "claim_expires_at"),
        Index("ix_article_jobs_article_created", "article_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    requested_mode: Mapped[str] = mapped_column(String(24))
    stage: Mapped[str] = mapped_column(String(16), default="fetch")
    status: Mapped[str] = mapped_column(String(24), default="queued")
    temporary_html_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    article: Mapped[Article] = relationship(back_populates="processing_jobs")
    attempts: Mapped[list["ArticleProcessingAttempt"]] = relationship(back_populates="job")


class ArticleProcessingAttempt(Base):
    __tablename__ = "article_processing_attempts"
    __table_args__ = (Index("ix_article_attempts_job", "job_id", "started_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_processing_jobs.id", ondelete="CASCADE")
    )
    stage: Mapped[str] = mapped_column(String(16))
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="running")
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    job: Mapped[ArticleProcessingJob] = relationship(back_populates="attempts")
