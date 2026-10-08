"""Wikidata tables: the user's links (kept) and a cache of what Wikidata said (rebuildable).

`entity_external_ids` holds decisions, like MARC 024 with $2 wikidata: it travels in the
authority export. Everything named wikidata_* is a copy or bookkeeping and refills from the QIDs.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SCHEMES = ("wikidata", "viaf", "isni", "lcnaf")


class EntityExternalId(Base):
    """An authority root's identifier in another file: one per scheme, a QID on one root only."""

    __tablename__ = "entity_external_ids"
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
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    state: Mapped[str] = mapped_column(String(16))
    redirect_to: Mapped[str | None] = mapped_column(String(16))
    revision: Mapped[int | None] = mapped_column(BigInteger)
    labels: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    aliases: Mapped[dict[str, list[str]]] = mapped_column(JSONB, default=dict)
    descriptions: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    instance_of: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    different_from: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    ids: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    sitelinks: Mapped[int] = mapped_column(Integer, default=0)
    # A candidate is fetched without claims; the claims come when the user links the item.
    claims_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataClass(Base):
    """A class (P31 value) and what it is a subclass of (P279), for the type check."""

    __tablename__ = "wikidata_classes"
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    label_en: Mapped[str | None] = mapped_column(Text)
    subclass_of: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataSearch(Base):
    """What a name search found (nothing included), so the same search waits 30 days."""

    __tablename__ = "wikidata_searches"
    language: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(Text, primary_key=True)
    qids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataCandidate(Base):
    """A QID the engine suggests for a root; only the user links it. Dismissed ones stay out."""

    __tablename__ = "wikidata_candidates"
    entity_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE"), primary_key=True
    )
    qid: Mapped[str] = mapped_column(String(16), primary_key=True)
    score: Mapped[float] = mapped_column(Float)
    reasons: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WikidataRun(Base):
    """A candidate search or a refresh: queued by the scheduler or a button, worked in batches."""

    __tablename__ = "wikidata_runs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(
        String(16), default="queued", server_default=text("'queued'")
    )
    # A run for one root (the entity page's button); NULL for a sweep.
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("nlp_entities.id", ondelete="CASCADE")
    )
    cursor: Mapped[str | None] = mapped_column(Text)
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
    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
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
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
    total_ms: Mapped[int] = mapped_column(BigInteger, default=0)
