import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ColumnElement,
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
from sqlalchemy.ext.hybrid import hybrid_property
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
        Index("ix_nlp_jobs_created_at", "created_at"),
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
    # The latest spelling NLP saw; the user's own choice goes in preferred_text, which NLP
    # never writes.
    display_text: Mapped[str] = mapped_column(Text)
    # NULL: this entity is a root. Otherwise the root it is a variant of, never another variant.
    authority_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="RESTRICT")
    )
    preferred_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="provisional", server_default=text("'provisional'")
    )
    # A name that may stand for several people: NER never folds it into a longer name.
    ambiguous: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text)
    # Where the name came from: "ner" (an article), "seed" (the country seed), "wikidata" (a
    # label of the linked item) or "user" (an import or a name the user added).
    name_source: Mapped[str] = mapped_column(
        String(16), default="ner", server_default=text("'ner'")
    )

    @hybrid_property
    def name(self) -> str:
        """The name to show: the user's preferred one, else the latest spelling NLP saw."""
        return self.preferred_text or self.display_text

    @name.inplace.expression
    @classmethod
    def _name_expression(cls) -> ColumnElement[str]:
        return func.coalesce(cls.preferred_text, cls.display_text)


class EntityDistinct(Base):
    """Two entities the user said are different, so duplicate suggestions skip the pair."""

    __tablename__ = "entity_distinct"
    __table_args__ = (CheckConstraint("a_id < b_id", name="ck_entity_distinct_ordered"),)
    a_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE"), primary_key=True
    )
    b_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityAuthorityChange(Base):
    """What changed in the authority file and when."""

    __tablename__ = "entity_authority_changes"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[uuid.UUID]
    other_id: Mapped[uuid.UUID | None]
    before: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EntityAuthorityRun(Base):
    """A merge, split or reindex that works through articles in batches, resuming from `cursor`.

    A merge or split first moves article rows, then reindexes the root's articles (its names
    changed); a reindex run, which a rename starts, does only the second part.
    """

    __tablename__ = "entity_authority_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="RESTRICT"))
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), default="running", server_default=text("'running'")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityRelation(Base):
    """A see-also link the user stated between two roots; the inverse is read, never stored."""

    __tablename__ = "entity_relations"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="CASCADE"))
    # Partial dates: "2009", "2021-10" or "2021-10-28".
    valid_from: Mapped[str | None] = mapped_column(String(10))
    valid_to: Mapped[str | None] = mapped_column(String(10))
    note: Mapped[str | None] = mapped_column(Text)
    source_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


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
    # Always a root: search, the graph and the dossiers count this one.
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="RESTRICT"))
    # The variant the article actually named, when it is not the root itself.
    observed_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="RESTRICT")
    )
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
