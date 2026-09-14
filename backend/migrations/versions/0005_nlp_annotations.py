"""Add canonical, versioned NLP annotations and durable processing intent.

Revision ID: 0005
Revises: 0004

The downgrade removes only Phase 5 NLP data. Canonical articles, extracted
content, source provenance, retained objects, and search coordination remain.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    json_type = postgresql.JSONB()
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "article_nlp_states",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("processor_name", sa.String(64), nullable=False),
        sa.Column("requested_generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("completed_generation", sa.Integer(), server_default="0", nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("processor_version", sa.String(64), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("updated_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("article_id", "processor_name", name="uq_article_nlp_state"),
    )
    op.create_table(
        "nlp_jobs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "state_id",
            uuid_type,
            sa.ForeignKey("article_nlp_states.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("processor_name", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("processor_version", sa.String(64), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("claim_token", sa.String(64), unique=True),
        sa.Column("claim_expires_at", timestamp),
        sa.Column("error_category", sa.String(64)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp),
        sa.UniqueConstraint(
            "article_id", "processor_name", "generation", name="uq_nlp_job_generation"
        ),
    )
    op.create_index(
        "ix_nlp_jobs_due", "nlp_jobs", ["status", "next_attempt_at", "claim_expires_at"]
    )
    op.create_table(
        "nlp_processor_runs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "job_id", uuid_type, sa.ForeignKey("nlp_jobs.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("processor_name", sa.String(64), nullable=False),
        sa.Column("processor_version", sa.String(64), nullable=False),
        sa.Column("algorithm_version", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128)),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.Column("started_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp),
    )
    op.create_index(
        "ix_nlp_runs_article_processor",
        "nlp_processor_runs",
        ["article_id", "processor_name", "started_at"],
    )
    op.create_table(
        "nlp_entities",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "language", "entity_type", "normalized_text", name="uq_nlp_entity_identity"
        ),
    )
    op.create_table(
        "nlp_keywords",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.UniqueConstraint("language", "kind", "normalized_text", name="uq_nlp_keyword_identity"),
    )
    op.create_table(
        "article_nlp_entities",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "entity_id",
            uuid_type,
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            uuid_type,
            sa.ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original_label", sa.String(64)),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("occurrences", json_type, nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        "ix_article_nlp_entities_current", "article_nlp_entities", ["article_id", "is_current"]
    )
    op.create_table(
        "article_nlp_keywords",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "keyword_id",
            uuid_type,
            sa.ForeignKey("nlp_keywords.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            uuid_type,
            sa.ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("occurrences", json_type, nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        "ix_article_nlp_keywords_current", "article_nlp_keywords", ["article_id", "is_current"]
    )
    op.create_table(
        "article_country_annotations",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            uuid_type,
            sa.ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("inferred", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("rule_version", sa.String(64), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("occurrences", json_type, nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        "ix_article_country_current",
        "article_country_annotations",
        ["article_id", "role", "is_current"],
    )
    op.create_table(
        "nlp_stop_word_revisions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("words", json_type, nullable=False),
        sa.Column("configuration_fingerprint", sa.String(64), nullable=False),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("language", "revision", name="uq_nlp_stop_word_revision"),
    )
    op.create_table(
        "nlp_reprocessing_runs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("status", sa.String(24), server_default="queued", nullable=False),
        sa.Column("processor_names", json_type, nullable=False),
        sa.Column("selection", json_type, nullable=False),
        sa.Column("article_cursor", uuid_type),
        sa.Column("scanned_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("enqueued_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", timestamp, server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", timestamp),
    )


def downgrade() -> None:
    op.drop_table("nlp_reprocessing_runs")
    op.drop_table("nlp_stop_word_revisions")
    op.drop_table("article_country_annotations")
    op.drop_table("article_nlp_keywords")
    op.drop_table("article_nlp_entities")
    op.drop_table("nlp_keywords")
    op.drop_table("nlp_entities")
    op.drop_table("nlp_processor_runs")
    op.drop_table("nlp_jobs")
    op.drop_table("article_nlp_states")
