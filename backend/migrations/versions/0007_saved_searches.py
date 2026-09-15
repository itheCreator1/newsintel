"""Add named saved investigations.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    op.create_table(
        "saved_searches",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_saved_searches_name_present"),
        sa.CheckConstraint("state_version > 0", name="ck_saved_searches_state_version"),
    )
    op.create_index(
        "uq_saved_searches_user_name",
        "saved_searches",
        ["user_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index("ix_saved_searches_user_order", "saved_searches", ["user_id", "name", "id"])


def downgrade() -> None:
    op.drop_table("saved_searches")
