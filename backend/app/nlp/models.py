import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ArticleNlpState(Base):
    __tablename__ = "article_nlp_states"
    __table_args__ = (
        UniqueConstraint("article_id", "processor_name", name="uq_article_nlp_state"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    processor_name: Mapped[str] = mapped_column(String(64))
    requested_generation: Mapped[int] = mapped_column(Integer, default=1)
    completed_generation: Mapped[int] = mapped_column(Integer, default=0)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(64))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NlpJob(Base):
    __tablename__ = "nlp_jobs"
    __table_args__ = (
        UniqueConstraint(
            "article_id", "processor_name", "generation", name="uq_nlp_job_generation"
        ),
        Index("ix_nlp_jobs_due", "status", "next_attempt_at", "claim_expires_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    state_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_nlp_states.id", ondelete="CASCADE")
    )
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    processor_name: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(64))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
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


class NlpProcessorRun(Base):
    __tablename__ = "nlp_processor_runs"
    __table_args__ = (
        Index("ix_nlp_runs_article_processor", "article_id", "processor_name", "started_at"),
        Index("ix_nlp_runs_completed", "completed_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_jobs.id", ondelete="CASCADE"))
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    processor_name: Mapped[str] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(64))
    algorithm_version: Mapped[str] = mapped_column(String(128))
    model_version: Mapped[str | None] = mapped_column(String(128))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str | None] = mapped_column(Text)
    # A failed run's category, kept here because the job's is overwritten by its next attempt.
    error_category: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ArticleLanguageAnnotation(Base):
    __tablename__ = "article_language_annotations"
    __table_args__ = (Index("ix_article_language_current", "article_id", "is_current"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE")
    )
    language: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class Entity(Base):
    __tablename__ = "nlp_entities"
    __table_args__ = (
        UniqueConstraint(
            "language", "entity_type", "normalized_text", name="uq_nlp_entity_identity"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    language: Mapped[str] = mapped_column(String(16))
    entity_type: Mapped[str] = mapped_column(String(32))
    normalized_text: Mapped[str] = mapped_column(Text)
    display_text: Mapped[str] = mapped_column(Text)


class Keyword(Base):
    __tablename__ = "nlp_keywords"
    __table_args__ = (
        UniqueConstraint("language", "kind", "normalized_text", name="uq_nlp_keyword_identity"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    language: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24))
    normalized_text: Mapped[str] = mapped_column(Text)
    display_text: Mapped[str] = mapped_column(Text)


class ArticleEntity(Base):
    __tablename__ = "article_nlp_entities"
    __table_args__ = (
        Index("ix_article_nlp_entities_current", "article_id", "is_current"),
        Index(
            "ix_article_nlp_entities_entity_article",
            "entity_id",
            "article_id",
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="RESTRICT"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE")
    )
    original_label: Mapped[str | None] = mapped_column(String(64))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    relevance: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class ArticleKeyword(Base):
    __tablename__ = "article_nlp_keywords"
    __table_args__ = (Index("ix_article_nlp_keywords_current", "article_id", "is_current"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_keywords.id", ondelete="RESTRICT")
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE")
    )
    occurrence_count: Mapped[int] = mapped_column(Integer)
    raw_score: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class ArticleCountryAnnotation(Base):
    __tablename__ = "article_country_annotations"
    __table_args__ = (Index("ix_article_country_current", "article_id", "role", "is_current"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE")
    )
    country_code: Mapped[str] = mapped_column(String(2))
    role: Mapped[str] = mapped_column(String(24))
    inferred: Mapped[bool] = mapped_column(Boolean, default=False)
    rule_version: Mapped[str] = mapped_column(String(128))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSON)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class StopWordRevision(Base):
    __tablename__ = "nlp_stop_word_revisions"
    __table_args__ = (UniqueConstraint("language", "revision", name="uq_nlp_stop_word_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    language: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    words: Mapped[list[str]] = mapped_column(JSON)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NlpReprocessingRun(Base):
    __tablename__ = "nlp_reprocessing_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    processor_names: Mapped[list[str]] = mapped_column(JSON)
    selection: Mapped[dict[str, object]] = mapped_column(JSON)
    article_cursor: Mapped[uuid.UUID | None]
    scanned_count: Mapped[int] = mapped_column(Integer, default=0)
    enqueued_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
