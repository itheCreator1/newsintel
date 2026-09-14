"""Add durable search indexing coordination.

Revision ID: 0004
Revises: 0003

Downgrade removes only derived search coordination. It preserves canonical
articles, extracted content, and feed provenance.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    op.create_table(
        "article_search_states",
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("requested_revision", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "search_index_targets",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("index_name", sa.String(255), nullable=False, unique=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "search_deliveries",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_id",
            uuid_type,
            sa.ForeignKey("search_index_targets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_revision", sa.Integer(), nullable=False),
        sa.Column("indexed_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(64), unique=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("article_id", "target_id", name="uq_search_delivery_article_target"),
    )
    op.create_index(
        "ix_search_deliveries_due",
        "search_deliveries",
        ["status", "next_attempt_at", "claim_expires_at"],
    )
    op.create_table(
        "search_rebuilds",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "target_id",
            uuid_type,
            sa.ForeignKey("search_index_targets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("active_key", sa.String(16)),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scan_cursor", uuid_type),
        sa.Column("scanned_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("coordinator_token", sa.String(64), unique=True),
        sa.Column("coordinator_expires_at", sa.DateTime(timezone=True)),
        sa.Column("cutover_intent_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("uq_search_rebuild_active", "search_rebuilds", ["active_key"], unique=True)
    op.create_table(
        "source_search_refreshes",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "feed_id", uuid_type, sa.ForeignKey("feeds.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("article_cursor", uuid_type),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(64), unique=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index(
        "ix_source_search_refresh_due", "source_search_refreshes", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_table("source_search_refreshes")
    op.drop_table("search_rebuilds")
    op.drop_table("search_deliveries")
    op.drop_table("search_index_targets")
    op.drop_table("article_search_states")
