"""Wikidata tables: the user's links (kept) and a cache of what Wikidata said (rebuildable).

`entity_external_ids` holds decisions, like MARC 024 with $2 wikidata: it travels in the
authority export. Everything named wikidata_* is a copy or bookkeeping and refills from the QIDs.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, one_of

SCHEMES = ("wikidata", "viaf", "isni", "lcnaf")
# Each request to Wikidata is counted by its kind and outcome; any outcome but ok pauses them.
REQUEST_KINDS = ("search", "info", "entities", "claims")
REQUEST_OUTCOMES = ("ok", "rate_limited", "blocked", "error")
PAUSE_REASONS = REQUEST_OUTCOMES[1:]


class EntityExternalId(Base):
    """An authority root's identifier in another file: one per scheme, a QID on one root only."""

    __tablename__ = "entity_external_ids"
    __table_args__ = (
        CheckConstraint(one_of("scheme", SCHEMES), name="ck_entity_external_id_scheme"),
        CheckConstraint(
            one_of("source", ("user", "wikidata")), name="ck_entity_external_id_source"
        ),
        CheckConstraint(
            "scheme <> 'wikidata' OR value ~ '^Q[1-9][0-9]*$'", name="ck_entity_external_id_qid"
        ),
        Index(
            "uq_entity_external_id_wikidata",
            "value",
            unique=True,
            postgresql_where=text("scheme = 'wikidata'"),
        ),
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="RESTRICT"), primary_key=True
    )
    scheme: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    # "user" for a link the user approved; "wikidata" for VIAF/ISNI/LCNAF read off the item.
    source: Mapped[str] = mapped_column(String(16), default="user", server_default=text("'user'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataItem(Base):
    """The few fields we keep of an item; refetched only when its revision changes."""

    __tablename__ = "wikidata_items"
    __table_args__ = (
        CheckConstraint(
            one_of("state", ("ok", "redirected", "missing")), name="ck_wikidata_item_state"
        ),
    )
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    redirect_to: Mapped[str | None] = mapped_column(String(16))
    revision: Mapped[int | None] = mapped_column(BigInteger)
    labels: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    aliases: Mapped[dict[str, list[str]]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    descriptions: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    instance_of: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    different_from: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    ids: Mapped[dict[str, str]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    sitelinks: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # A candidate is fetched without claims; the claims come when the user links the item.
    claims_fetched: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataClass(Base):
    """A class (P31 value) and what it is a subclass of (P279), for the type check."""

    __tablename__ = "wikidata_classes"
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    label_en: Mapped[str | None] = mapped_column(Text)
    subclass_of: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataSearch(Base):
    """What a name search found (nothing included), so the same search waits 30 days."""

    __tablename__ = "wikidata_searches"
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(Text, primary_key=True)
    # A plain string: the column above shadows sqlalchemy's text() in this class body.
    qids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list, server_default="{}")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataCandidate(Base):
    """A QID the engine suggests for a root; only the user links it. Dismissed ones stay out."""

    __tablename__ = "wikidata_candidates"
    __table_args__ = (
        Index("ix_wikidata_candidates_qid", "qid"),
        Index("ix_wikidata_candidates_score", text("score DESC")),
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE"), primary_key=True
    )
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("'{}'::text[]")
    )
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # The root's one candidate with its exact label and the right type: "approve all exact".
    exact: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataRun(Base):
    """A candidate search or a refresh: queued by the scheduler or a button, worked in batches."""

    __tablename__ = "wikidata_runs"
    __table_args__ = (
        CheckConstraint(one_of("kind", ("candidates", "refresh")), name="ck_wikidata_run_kind"),
        CheckConstraint(
            one_of("status", ("queued", "running", "finished", "failed", "stopped")),
            name="ck_wikidata_run_status",
        ),
        Index("ix_wikidata_runs_status", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default=text("'queued'")
    )
    # A run for one root (the entity page's button); NULL for a sweep.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE")
    )
    # A sweep's last root done, so a run stopped by a pause or the budget goes on from there.
    cursor: Mapped[str | None] = mapped_column(Text)
    # The worker holding the run; another may take it once the claim expires.
    claim_token: Mapped[uuid.UUID | None] = mapped_column()
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A refresh of every linked item, not only those due (`wikidata refresh --all`).
    force: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    # A refresh for a new link whose item was not cached: its labels come with the fetch.
    add_labels: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    requests: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    checked: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    changed: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    redirected: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    missing: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    errors: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WikidataThrottle(Base):
    """The one row every Wikidata request locks: pace, pauses and today's count."""

    __tablename__ = "wikidata_throttle"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_wikidata_throttle_one_row"),
        CheckConstraint(
            one_of("pause_reason", PAUSE_REASONS), name="ck_wikidata_throttle_pause_reason"
        ),
    )
    # Always 1, written by migration 0001; no sequence numbers it.
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    next_request_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pause_reason: Mapped[str | None] = mapped_column(String(16))
    error_strikes: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    last_rate_limited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    day: Mapped[date | None] = mapped_column(Date)
    requests_today: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class WikidataRequestCount(Base):
    """Requests sent per day, kind and outcome, with their summed time: our load on Wikidata."""

    __tablename__ = "wikidata_request_counts"
    __table_args__ = (
        CheckConstraint(one_of("kind", REQUEST_KINDS), name="ck_wikidata_request_counts_kind"),
        CheckConstraint(
            one_of("outcome", REQUEST_OUTCOMES), name="ck_wikidata_request_counts_outcome"
        ),
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(BigInteger, default=0)
