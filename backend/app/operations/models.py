import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MaintenanceRun(Base):
    """One run of a maintenance task (`retention` today): when, what it deleted, or its error.

    The Processes page reads the newest one; rows older than the job retention are pruned.
    """

    __tablename__ = "maintenance_runs"
    __table_args__ = (Index("ix_maintenance_runs_kind_started", "kind", "started_at"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(32))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rows deleted per table; NULL while running or after a failure.
    deleted: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
