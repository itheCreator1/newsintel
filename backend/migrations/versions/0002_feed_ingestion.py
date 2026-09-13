"""Feed ingestion and canonical article archive."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_country", sa.String(2)),
        sa.Column("expected_language", sa.String(16)),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("fetching_mode", sa.String(16), nullable=False),
        sa.Column("etag", sa.Text()),
        sa.Column("last_modified", sa.Text()),
        sa.Column(
            "next_poll_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True)),
        sa.Column("claim_token", sa.String(64)),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True)),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index("ix_feeds_due", "feeds", ["enabled", "retired_at", "next_poll_at"])
    op.create_index("ix_feeds_retired_at", "feeds", ["retired_at"])
    op.create_table(
        "feed_fetches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("claim_token", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer()),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("invalid_entry_count", sa.Integer(), nullable=False),
        sa.Column("new_article_count", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Text()),
        sa.Column("last_modified", sa.Text()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index("ix_feed_fetches_history", "feed_fetches", ["feed_id", "started_at"])
    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title_hash", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "first_discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_url", name="uq_articles_normalized_url"),
    )
    op.create_index("ix_articles_discovery", "articles", ["first_discovered_at", "id"])
    op.create_index("ix_articles_title_hash", "articles", ["normalized_title_hash"])
    op.create_table(
        "feed_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("guid", sa.Text()),
        sa.Column("feed_title", sa.Text(), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "article_id", name="uq_feed_articles_feed_article"),
        sa.UniqueConstraint("feed_id", "guid", name="uq_feed_articles_feed_guid"),
    )
    op.create_index("ix_feed_articles_article", "feed_articles", ["article_id", "discovered_at"])


def downgrade() -> None:
    op.drop_table("feed_articles")
    op.drop_table("articles")
    op.drop_table("feed_fetches")
    op.drop_table("feeds")
