"""The authority file: entities (roots and their variants), what the user said about them, and
the runs that carry a merge, split or rename through the articles.

NLP writes entities as it finds names; everything else here is the user's.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ColumnElement,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, one_of

# Every type a root can be shown as. NER writes all but TOPIC; the user may pick any.
ENTITY_TYPES = ("PERSON", "ORG", "GPE", "LOCATION", "EVENT", "PRODUCT", "TOPIC", "OTHER")
AUTHORITY_ACTIONS = (
    "merged",
    "split",
    "renamed",
    "status_changed",
    "ambiguous_changed",
    "distinct_added",
    "distinct_removed",
    "relation_added",
    "relation_changed",
    "relation_removed",
    "wikidata_linked",
    "wikidata_unlinked",
    "wikidata_redirected",
    "wikidata_missing",
    "type_changed",
    "hidden_changed",
    "claim_added",
    "claim_removed",
)
RELATION_TYPES = ("succeeded_by", "part_of", "member_of", "leader_of", "related")
# Partial dates: "2009", "2021-10" or "2021-10-28".
PARTIAL_DATE = r"^\d{4}(-\d{2}(-\d{2})?)?$"


class Entity(Base):
    __tablename__ = "nlp_entities"
    __table_args__ = (
        UniqueConstraint(
            "language", "entity_type", "normalized_text", name="uq_nlp_entity_identity"
        ),
        CheckConstraint(
            "preferred_type IS NULL OR " + one_of("preferred_type", ENTITY_TYPES),
            name="ck_nlp_entity_preferred_type",
        ),
        CheckConstraint(one_of("entity_type", ENTITY_TYPES), name="ck_nlp_entities_entity_type"),
        CheckConstraint(
            one_of("status", ("provisional", "established")), name="ck_nlp_entity_status"
        ),
        CheckConstraint(
            one_of("name_source", ("ner", "seed", "wikidata", "user")),
            name="ck_nlp_entity_name_source",
        ),
        CheckConstraint(
            "authority_id IS NULL OR authority_id <> id", name="ck_nlp_entity_not_own_authority"
        ),
        # The type and the "not a name" mark are a root's; a variant shows its root's.
        CheckConstraint(
            "authority_id IS NULL OR (preferred_type IS NULL AND NOT hidden)",
            name="ck_nlp_entity_root_only_choices",
        ),
        Index(
            "ix_nlp_entities_authority",
            "authority_id",
            postgresql_where=text("authority_id IS NOT NULL"),
        ),
        # Every entities job reads the few ambiguous names; keep that off a full table scan.
        Index(
            "ix_nlp_entities_ambiguous",
            "language",
            "entity_type",
            postgresql_where=text("ambiguous"),
        ),
        # Name search with a typo or part of a name (pg_trgm).
        Index(
            "ix_nlp_entities_normalized_trgm",
            "normalized_text",
            postgresql_using="gin",
            postgresql_ops={"normalized_text": "gin_trgm_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    language: Mapped[str] = mapped_column(String(16))
    # The model's type: part of the identity NER finds the name by, so it never changes. The
    # user's own type goes in preferred_type, which NLP never writes; `kind` is the one to show.
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
    preferred_type: Mapped[str | None] = mapped_column(String(32))
    # Not a name ("Last week", "Tuesday"): kept with its articles, left out of the search index,
    # the graph, clustering and the suggestion queues.
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
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

    @hybrid_property
    def kind(self) -> str:
        """The type to show and filter by: the user's, else the model's."""
        return self.preferred_type or self.entity_type

    @kind.inplace.expression
    @classmethod
    def _kind_expression(cls) -> ColumnElement[str]:
        return func.coalesce(cls.preferred_type, cls.entity_type)


class EntityNameClaim(Base):
    """A root that takes every type of one name: the roots NER made of it under other types
    were merged in when the claim was made, and a name NER finds later under a new type joins
    the root as it is created."""

    __tablename__ = "entity_name_claims"
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    normalized_text: Mapped[str] = mapped_column(Text, primary_key=True)
    root_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    __table_args__ = (
        CheckConstraint(
            one_of("action", AUTHORITY_ACTIONS), name="ck_entity_authority_change_action"
        ),
        Index("ix_entity_authority_changes_entity", "entity_id", "created_at"),
    )
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
    __table_args__ = (
        CheckConstraint(
            one_of("kind", ("merge", "split", "reindex")), name="ck_entity_authority_run_kind"
        ),
        CheckConstraint(
            one_of("status", ("running", "finished", "failed")),
            name="ck_entity_authority_run_status",
        ),
    )
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
    __table_args__ = (
        CheckConstraint(one_of("relation_type", RELATION_TYPES), name="ck_entity_relation_type"),
        CheckConstraint("subject_id <> object_id", name="ck_entity_relation_not_self"),
        # One row per symmetric pair.
        CheckConstraint(
            "relation_type <> 'related' OR subject_id < object_id",
            name="ck_entity_relation_related_ordered",
        ),
        CheckConstraint(
            f"valid_from IS NULL OR valid_from ~ '{PARTIAL_DATE}'",
            name="ck_entity_relation_valid_from",
        ),
        CheckConstraint(
            f"valid_to IS NULL OR valid_to ~ '{PARTIAL_DATE}'", name="ck_entity_relation_valid_to"
        ),
        # An expression index, not a constraint: a second period of the same link is a second row.
        Index(
            "uq_entity_relation",
            "subject_id",
            "relation_type",
            "object_id",
            text("coalesce(valid_from, '')"),
            unique=True,
        ),
        Index("ix_entity_relations_object", "object_id"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("nlp_entities.id", ondelete="CASCADE"))
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
