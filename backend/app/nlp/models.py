import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.ids import uuid7
from app.db.base import Base, one_of

# The values each status column holds; the CHECKs below and migration 0004 are built from them.
JOB_STATUSES = ("queued", "running", "retrying", "succeeded", "failed", "superseded")
RUN_OUTCOMES = ("success", "unsupported_language", "disabled", "failed")
STATE_STATUSES = ("queued", "running", "retrying", *RUN_OUTCOMES)
REPROCESSING_STATUSES = ("queued", "scanning", "succeeded", "stopped", "failed")
KEYWORD_KINDS = ("keyword", "keyphrase")
COUNTRY_ROLES = ("primary", "mentioned")


class ArticleNlpState(Base):
    __tablename__ = "article_nlp_states"
    __table_args__ = (
        UniqueConstraint("article_id", "processor_name", name="uq_article_nlp_state"),
        CheckConstraint(one_of("status", STATE_STATUSES), name="ck_article_nlp_states_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    processor_name: Mapped[str] = mapped_column(String(64))
    requested_generation: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    completed_generation: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(64))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default=text("'queued'")
    )
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
        Index("ix_nlp_jobs_created_at", "created_at"),
        CheckConstraint(one_of("status", JOB_STATUSES), name="ck_nlp_jobs_status"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    state_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("article_nlp_states.id", ondelete="CASCADE")
    )
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    processor_name: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(Integer)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    processor_version: Mapped[str] = mapped_column(String(64))
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default=text("'queued'")
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
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
        CheckConstraint(one_of("outcome", RUN_OUTCOMES), name="ck_nlp_processor_runs_outcome"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    # Indexed: retention deletes jobs, and each delete looks up the job's runs here.
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_jobs.id", ondelete="CASCADE"), index=True
    )
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
    __table_args__ = (
        Index("ix_article_language_current", "article_id", "is_current"),
        Index(
            "uq_article_language_annotations_current",
            "article_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    # Indexed: retention deletes runs, and each delete looks up the run's rows here.
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"), index=True
    )
    language: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float] = mapped_column(Float)
    margin: Mapped[float] = mapped_column(Float)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class NlpLanguageSetting(Base):
    """Per-language switches turned on in Settings; a language without a row is off."""

    __tablename__ = "nlp_language_settings"
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    ner_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Keyword(Base):
    __tablename__ = "nlp_keywords"
    __table_args__ = (
        UniqueConstraint("language", "kind", "normalized_text", name="uq_nlp_keyword_identity"),
        CheckConstraint(one_of("kind", KEYWORD_KINDS), name="ck_nlp_keywords_kind"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    language: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(24))
    normalized_text: Mapped[str] = mapped_column(Text)
    display_text: Mapped[str] = mapped_column(Text)


class ArticleEntity(Base):
    __tablename__ = "article_nlp_entities"
    __table_args__ = (
        Index("ix_article_nlp_entities_current", "article_id", "is_current"),
        # Every row, not only current ones: merge, split and in-use look up past rows too.
        Index("ix_article_nlp_entities_entity_article", "entity_id", "article_id"),
        Index(
            "ix_article_nlp_entities_observed",
            "observed_entity_id",
            postgresql_where=text("observed_entity_id IS NOT NULL"),
        ),
        Index(
            "uq_article_nlp_entities_current",
            "article_id",
            "entity_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    # Always a root: search, the graph and the dossiers count this one.
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="RESTRICT"))
    # The variant the article actually named, when it is not the root itself.
    observed_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="RESTRICT")
    )
    # Indexed: retention deletes runs, and each delete looks up the run's rows here.
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"), index=True
    )
    original_label: Mapped[str | None] = mapped_column(String(64))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    relevance: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class ArticleKeyword(Base):
    __tablename__ = "article_nlp_keywords"
    __table_args__ = (
        Index("ix_article_nlp_keywords_current", "article_id", "is_current"),
        Index(
            "uq_article_nlp_keywords_current",
            "article_id",
            "keyword_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    keyword_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_keywords.id", ondelete="RESTRICT"), index=True
    )
    # Indexed: retention deletes runs, and each delete looks up the run's rows here.
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"), index=True
    )
    occurrence_count: Mapped[int] = mapped_column(Integer)
    raw_score: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class ArticleCountryAnnotation(Base):
    __tablename__ = "article_country_annotations"
    __table_args__ = (
        Index("ix_article_country_current", "article_id", "role", "is_current"),
        Index(
            "uq_article_country_annotations_current",
            "article_id",
            "role",
            "country_code",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        CheckConstraint(one_of("role", COUNTRY_ROLES), name="ck_article_country_annotations_role"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    article_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"))
    # Indexed: retention deletes runs, and each delete looks up the run's rows here.
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"), index=True
    )
    country_code: Mapped[str] = mapped_column(String(2))
    role: Mapped[str] = mapped_column(String(24))
    inferred: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    rule_version: Mapped[str] = mapped_column(String(128))
    occurrence_count: Mapped[int] = mapped_column(Integer)
    occurrences: Mapped[list[dict[str, object]]] = mapped_column(JSONB)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))


class StopWordRevision(Base):
    __tablename__ = "nlp_stop_word_revisions"
    __table_args__ = (UniqueConstraint("language", "revision", name="uq_nlp_stop_word_revision"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    language: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer)
    words: Mapped[list[str]] = mapped_column(JSONB)
    configuration_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NlpReprocessingRun(Base):
    __tablename__ = "nlp_reprocessing_runs"
    __table_args__ = (
        CheckConstraint(
            one_of("status", REPROCESSING_STATUSES), name="ck_nlp_reprocessing_runs_status"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    status: Mapped[str] = mapped_column(
        String(24), default="queued", server_default=text("'queued'")
    )
    processor_names: Mapped[list[str]] = mapped_column(JSONB)
    selection: Mapped[dict[str, object]] = mapped_column(JSONB)
    article_cursor: Mapped[uuid.UUID | None]
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    enqueued_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
