import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Monitor(Base):
    """A durable watch over one investigation state.

    Stores the query snapshot plus compact evaluation state; never the matched articles.
    """

    __tablename__ = "monitors"
    __table_args__ = (
        Index("uq_monitors_user_name", "user_id", text("lower(name)"), unique=True),
        Index("ix_monitors_user_order", "user_id", "name", "id"),
        Index("ix_monitors_user_activity", "user_id", "latest_match_at", "id"),
        Index(
            "ix_monitors_due",
            "next_evaluation_at",
            "id",
            postgresql_where=text("enabled"),
        ),
        CheckConstraint("length(btrim(name)) > 0", name="ck_monitors_name_present"),
        CheckConstraint(
            "kind IN ('search', 'entity', 'source', 'country', 'cluster')",
            name="ck_monitors_kind",
        ),
        CheckConstraint("state_version > 0", name="ck_monitors_state_version"),
        CheckConstraint("unseen_article_count >= 0", name="ck_monitors_unseen_articles"),
        CheckConstraint("unseen_cluster_count >= 0", name="ck_monitors_unseen_clusters"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(16))
    state_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    # Incremental evaluation high-water mark: the last (discovery time, article id) evaluated.
    eval_cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    eval_cursor_article_id: Mapped[uuid.UUID | None] = mapped_column()
    # Same boundary as of the analyst's last view; unseen activity is what lies between the two.
    viewed_cursor_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    viewed_cursor_article_id: Mapped[uuid.UUID | None] = mapped_column()
    latest_match_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_match_article_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("articles.id", ondelete="SET NULL")
    )
    unseen_article_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    unseen_cluster_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Evaluation lease and outcome, shaped like the article/NLP/cluster job rows.
    next_evaluation_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    claim_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
