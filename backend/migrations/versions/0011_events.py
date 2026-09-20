"""Add versioned events and their cluster and entity associations.

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "events",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("started_at", timestamp),
        sa.Column("ended_at", timestamp),
        sa.Column("primary_country", sa.String(2)),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("id", "algorithm_version", name="uq_events_id_version"),
        sa.CheckConstraint("status IN ('active', 'closed', 'superseded')", name="ck_events_status"),
        sa.CheckConstraint("ended_at >= started_at", name="ck_events_span"),
    )
    op.create_index("ix_events_status_time", "events", ["status", "ended_at", "id"])
    op.create_index("ix_events_country_time", "events", ["primary_country", "ended_at", "id"])
    op.create_index("ix_events_time", "events", ["ended_at", "id"])
    op.create_index("ix_events_algorithm", "events", ["algorithm_version", "id"])

    op.create_table(
        "event_clusters",
        sa.Column("event_id", uuid_type, nullable=False),
        sa.Column(
            "cluster_id",
            uuid_type,
            sa.ForeignKey("story_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "signals",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("joined_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "cluster_id"),
        sa.ForeignKeyConstraint(
            ["event_id", "algorithm_version"],
            ["events.id", "events.algorithm_version"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "algorithm_version", "cluster_id", name="uq_event_clusters_version_cluster"
        ),
    )
    op.create_index("ix_event_clusters_cluster", "event_clusters", ["cluster_id"])

    op.create_table(
        "event_entities",
        sa.Column(
            "event_id",
            uuid_type,
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "entity_id",
            uuid_type,
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("article_count > 0", name="ck_event_entities_count"),
    )
    op.create_index("ix_event_entities_entity", "event_entities", ["entity_id", "event_id"])


def downgrade() -> None:
    op.drop_table("event_entities")
    op.drop_table("event_clusters")
    op.drop_table("events")
