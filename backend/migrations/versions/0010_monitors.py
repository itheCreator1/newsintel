"""Add durable monitors.

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "monitors",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("eval_cursor_at", timestamp),
        sa.Column("eval_cursor_article_id", uuid_type),
        sa.Column("viewed_cursor_at", timestamp),
        sa.Column("viewed_cursor_article_id", uuid_type),
        sa.Column("latest_match_at", timestamp),
        sa.Column(
            "latest_match_article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
        ),
        sa.Column("unseen_article_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unseen_cluster_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_evaluation_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("claim_token", sa.String(64), unique=True),
        sa.Column("claim_expires_at", timestamp),
        sa.Column("last_evaluated_at", timestamp),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_monitors_name_present"),
        sa.CheckConstraint(
            "kind IN ('search', 'entity', 'source', 'country', 'cluster')",
            name="ck_monitors_kind",
        ),
        sa.CheckConstraint("state_version > 0", name="ck_monitors_state_version"),
        sa.CheckConstraint("unseen_article_count >= 0", name="ck_monitors_unseen_articles"),
        sa.CheckConstraint("unseen_cluster_count >= 0", name="ck_monitors_unseen_clusters"),
    )
    op.create_index(
        "uq_monitors_user_name", "monitors", ["user_id", sa.text("lower(name)")], unique=True
    )
    op.create_index("ix_monitors_user_order", "monitors", ["user_id", "name", "id"])
    op.create_index("ix_monitors_user_activity", "monitors", ["user_id", "latest_match_at", "id"])
    op.create_index(
        "ix_monitors_due",
        "monitors",
        ["next_evaluation_at", "id"],
        postgresql_where=sa.text("enabled"),
    )


def downgrade() -> None:
    op.drop_table("monitors")
