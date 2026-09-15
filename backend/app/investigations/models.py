import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SavedSearch(Base):
    __tablename__ = "saved_searches"
    __table_args__ = (
        Index("uq_saved_searches_user_name", "user_id", text("lower(name)"), unique=True),
        Index("ix_saved_searches_user_order", "user_id", "name", "id"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_saved_searches_name_present"),
        CheckConstraint("state_version > 0", name="ck_saved_searches_state_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    state_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
