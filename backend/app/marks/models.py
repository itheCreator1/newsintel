import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

NOTE_LENGTH = 4000


class ArticleMark(Base):
    """The reader's own marks on an article. The app has one user, so there is no user column."""

    __tablename__ = "article_marks"
    __table_args__ = (
        CheckConstraint(f"length(note) <= {NOTE_LENGTH}", name="ck_article_marks_note_length"),
        Index("ix_article_marks_starred", "article_id", postgresql_where=text("starred")),
    )

    article_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    starred: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
