"""Articles: one row per URL, its extracted text, and the jobs that fetch and extract it.

Feeds discover articles (app.feeds.models.FeedArticle links the two); everything else reads them.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, one_of

if TYPE_CHECKING:
    from app.feeds.models import FeedArticle

# A processing job's values; the CHECKs below and migration 0004 are built from them.
ACTIVE_STATUSES = ("queued", "running", "retrying")
JOB_STATUSES = (*ACTIVE_STATUSES, "succeeded", "failed")
ATTEMPT_STATUSES = ("running", "succeeded", "failed")
STAGES = ("fetch", "extract")
REQUESTED_MODES = ("full_text", "full_text_html")


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("normalized_url", name="uq_articles_normalized_url"),
        Index("ix_articles_discovery", "first_discovered_at", "id"),
        Index("ix_articles_title_hash", "normalized_title_hash"),
        Index("ix_articles_effective_date", text("COALESCE(published_at, first_discovered_at)")),
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
        Index("ix_article_jobs_completed", "completed_at"),
        # One active job per article.
        Index(
            "uq_article_jobs_active",
            "article_id",
            unique=True,
            postgresql_where=text(one_of("status", ACTIVE_STATUSES)),
        ),
        CheckConstraint(one_of("status", JOB_STATUSES), name="ck_article_processing_jobs_status"),
        CheckConstraint(one_of("stage", STAGES), name="ck_article_processing_jobs_stage"),
        CheckConstraint(
            one_of("requested_mode", REQUESTED_MODES),
            name="ck_article_processing_jobs_requested_mode",
        ),
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
    __table_args__ = (
        Index("ix_article_attempts_job", "job_id", "started_at"),
        CheckConstraint(
            one_of("status", ATTEMPT_STATUSES), name="ck_article_processing_attempts_status"
        ),
        CheckConstraint(one_of("stage", STAGES), name="ck_article_processing_attempts_stage"),
    )
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


# Article's discoveries name FeedArticle; importing its module registers it wherever Article is
# used, as when both lived in app.feeds.models. A plain module import, so either may load first.
import app.feeds.models  # noqa: E402, F401
