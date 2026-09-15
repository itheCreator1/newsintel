"""Add story clusters and durable clustering intent.

Revision ID: 0008
Revises: 0007

The downgrade removes only Phase 7 clustering data. Canonical articles, source
provenance, NLP annotations, and search coordination remain untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "story_clusters",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("article_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("source_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_published_at", timestamp),
        sa.Column("last_published_at", timestamp),
        sa.Column(
            "representative_article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_story_clusters_recency", "story_clusters", ["last_published_at", "id"])
    op.create_table(
        "story_cluster_members",
        # The article is the primary key, so an article belongs to at most one cluster.
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "cluster_id",
            uuid_type,
            sa.ForeignKey("story_clusters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("joined_at", timestamp, server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_story_cluster_members_cluster", "story_cluster_members", ["cluster_id", "article_id"]
    )
    op.create_table(
        "article_cluster_state",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("completed_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("article_id", name="uq_article_cluster_state"),
    )
    op.create_table(
        "cluster_jobs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "state_id",
            uuid_type,
            sa.ForeignKey("article_cluster_state.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("claim_token", sa.String(64), unique=True),
        sa.Column("claim_expires_at", timestamp),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp),
        sa.UniqueConstraint("article_id", "generation", name="uq_cluster_job_generation"),
    )
    op.create_index(
        "ix_cluster_jobs_due", "cluster_jobs", ["status", "next_attempt_at", "claim_expires_at"]
    )
    # Candidate support: shared current entities, and the effective publication date.
    # `ix_articles_title_hash` already covers the normalized title hash.
    op.create_index(
        "ix_article_nlp_entities_entity_article",
        "article_nlp_entities",
        ["entity_id", "article_id"],
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_articles_effective_date",
        "articles",
        [sa.text("COALESCE(published_at, first_discovered_at)")],
    )


def downgrade() -> None:
    op.drop_index("ix_articles_effective_date", table_name="articles")
    op.drop_index("ix_article_nlp_entities_entity_article", table_name="article_nlp_entities")
    op.drop_table("cluster_jobs")
    op.drop_table("article_cluster_state")
    op.drop_table("story_cluster_members")
    op.drop_table("story_clusters")
