"""Record event association runs, and index the finish times the operations page windows by.

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "event_association_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sweep", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("evaluated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
    )
    op.create_index("ix_event_association_runs_started", "event_association_runs", ["started_at"])
    # Windowed operations counts ("finished in the last N hours"): measured at 60k rows, each cut a
    # sequential scan of the table to a bitmap scan of the window (about 3x faster at 24 h).
    op.create_index("ix_article_jobs_completed", "article_processing_jobs", ["completed_at"])
    op.create_index("ix_cluster_jobs_completed", "cluster_jobs", ["completed_at"])
    op.create_index("ix_nlp_runs_completed", "nlp_processor_runs", ["completed_at"])
    op.create_index("ix_feed_fetches_started", "feed_fetches", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_feed_fetches_started", table_name="feed_fetches")
    op.drop_index("ix_nlp_runs_completed", table_name="nlp_processor_runs")
    op.drop_index("ix_cluster_jobs_completed", table_name="cluster_jobs")
    op.drop_index("ix_article_jobs_completed", table_name="article_processing_jobs")
    op.drop_index("ix_event_association_runs_started", table_name="event_association_runs")
    op.drop_table("event_association_runs")
