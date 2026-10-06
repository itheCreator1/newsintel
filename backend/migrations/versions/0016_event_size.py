"""Cache each event's story and article counts, to hide one-story events and sort by size.

Revision ID: 0016
Revises: 0015
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for column in ("cluster_count", "article_count"):
        op.add_column("events", sa.Column(column, sa.Integer(), server_default="0", nullable=False))
    op.execute(
        """
        UPDATE events SET cluster_count = sizes.clusters, article_count = sizes.articles
        FROM (
            SELECT event_clusters.event_id,
                   count(DISTINCT event_clusters.cluster_id) AS clusters,
                   count(DISTINCT story_cluster_members.article_id) AS articles
            FROM event_clusters
            LEFT JOIN story_cluster_members
                ON story_cluster_members.cluster_id = event_clusters.cluster_id
            GROUP BY event_clusters.event_id
        ) AS sizes
        WHERE sizes.event_id = events.id
        """
    )
    op.create_index(
        "ix_events_size", "events", ["algorithm_version", "article_count", "ended_at", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_events_size", table_name="events")
    op.drop_column("events", "article_count")
    op.drop_column("events", "cluster_count")
