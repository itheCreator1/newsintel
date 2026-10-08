"""Authority control for entities: roots, variants, distinct pairs and a change history.

Every existing entity becomes its own root (authority_id NULL), provisional, unambiguous, with
no preferred name, so nothing changes until the user ties a variant to a root.

Revision ID: 0018
Revises: 0017
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A variant points at its root; the service keeps it one level deep (no chains), which a
    # CHECK cannot do because it would have to read another row.
    op.add_column(
        "nlp_entities",
        sa.Column(
            "authority_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("nlp_entities", sa.Column("preferred_text", sa.Text(), nullable=True))
    op.add_column(
        "nlp_entities",
        sa.Column("status", sa.String(16), server_default=sa.text("'provisional'"), nullable=False),
    )
    op.add_column(
        "nlp_entities",
        sa.Column("ambiguous", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("nlp_entities", sa.Column("note", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_nlp_entity_status", "nlp_entities", "status IN ('provisional', 'established')"
    )
    op.create_check_constraint(
        "ck_nlp_entity_not_own_authority",
        "nlp_entities",
        "authority_id IS NULL OR authority_id <> id",
    )
    op.create_index(
        "ix_nlp_entities_authority",
        "nlp_entities",
        ["authority_id"],
        postgresql_where=sa.text("authority_id IS NOT NULL"),
    )
    # Every entities job reads the few ambiguous names; keep that off a full table scan.
    op.create_index(
        "ix_nlp_entities_ambiguous",
        "nlp_entities",
        ["language", "entity_type"],
        postgresql_where=sa.text("ambiguous"),
    )
    # The name the article used, when its entity_id was rewritten to the root (NULL: the same).
    op.add_column(
        "article_nlp_entities",
        sa.Column(
            "observed_entity_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.create_table(
        "entity_distinct",
        sa.Column(
            "a_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "b_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("a_id", "b_id"),
        # One row per pair: the smaller id first.
        sa.CheckConstraint("a_id < b_id", name="ck_entity_distinct_ordered"),
    )
    op.create_table(
        "entity_authority_changes",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("action", sa.String(32), nullable=False),
        # Plain ids: the history outlives any later change to the entities it names.
        sa.Column("entity_id", postgresql.UUID(), nullable=False),
        sa.Column("other_id", postgresql.UUID(), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "action IN ('merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed',"
            " 'distinct_added', 'distinct_removed')",
            name="ck_entity_authority_change_action",
        ),
    )
    op.create_index(
        "ix_entity_authority_changes_entity",
        "entity_authority_changes",
        ["entity_id", "created_at"],
    )
    op.create_table(
        "entity_authority_runs",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column(
            "entity_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), server_default=sa.text("'running'"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('merge', 'split')", name="ck_entity_authority_run_kind"),
        sa.CheckConstraint(
            "status IN ('running', 'finished', 'failed')", name="ck_entity_authority_run_status"
        ),
    )
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_nlp_entities_normalized_trgm ON nlp_entities"
        " USING gin (normalized_text gin_trgm_ops)"
    )


def downgrade() -> None:
    # pg_trgm stays installed: dropping an extension could break anything else that uses it.
    op.execute("DROP INDEX IF EXISTS ix_nlp_entities_normalized_trgm")
    op.drop_table("entity_authority_runs")
    op.drop_index("ix_entity_authority_changes_entity", table_name="entity_authority_changes")
    op.drop_table("entity_authority_changes")
    op.drop_table("entity_distinct")
    op.drop_column("article_nlp_entities", "observed_entity_id")
    op.drop_index("ix_nlp_entities_ambiguous", table_name="nlp_entities")
    op.drop_index("ix_nlp_entities_authority", table_name="nlp_entities")
    op.drop_constraint("ck_nlp_entity_not_own_authority", "nlp_entities", type_="check")
    op.drop_constraint("ck_nlp_entity_status", "nlp_entities", type_="check")
    for column in ("note", "ambiguous", "status", "preferred_text", "authority_id"):
        op.drop_column("nlp_entities", column)
