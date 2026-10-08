"""See-also links between authority roots, and their three history actions.

Revision ID: 0020
Revises: 0019
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PARTIAL_DATE = r"^\d{4}(-\d{2}(-\d{2})?)?$"
ACTIONS = (
    "'merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed',"
    " 'distinct_added', 'distinct_removed'"
)


def upgrade() -> None:
    op.create_table(
        "entity_relations",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column(
            "subject_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("relation_type", sa.String(24), nullable=False),
        sa.Column(
            "object_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.String(10), nullable=True),
        sa.Column("valid_to", sa.String(10), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "source_article_id",
            postgresql.UUID(),
            sa.ForeignKey("articles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "relation_type IN ('succeeded_by', 'part_of', 'member_of', 'leader_of', 'related')",
            name="ck_entity_relation_type",
        ),
        sa.CheckConstraint("subject_id <> object_id", name="ck_entity_relation_not_self"),
        # One row per symmetric pair.
        sa.CheckConstraint(
            "relation_type <> 'related' OR subject_id < object_id",
            name="ck_entity_relation_related_ordered",
        ),
        sa.CheckConstraint(
            f"valid_from IS NULL OR valid_from ~ '{PARTIAL_DATE}'",
            name="ck_entity_relation_valid_from",
        ),
        sa.CheckConstraint(
            f"valid_to IS NULL OR valid_to ~ '{PARTIAL_DATE}'", name="ck_entity_relation_valid_to"
        ),
    )
    # An expression index, not a constraint: a second period of the same link is a second row.
    op.execute(
        "CREATE UNIQUE INDEX uq_entity_relation ON entity_relations"
        " (subject_id, relation_type, object_id, coalesce(valid_from, ''))"
    )
    op.create_index("ix_entity_relations_object", "entity_relations", ["object_id"])
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action",
        "entity_authority_changes",
        f"action IN ({ACTIONS}, 'relation_added', 'relation_changed', 'relation_removed')",
    )


def downgrade() -> None:
    # The links and their history go; every entity, name and other change stays.
    op.execute(
        "DELETE FROM entity_authority_changes"
        " WHERE action IN ('relation_added', 'relation_changed', 'relation_removed')"
    )
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", f"action IN ({ACTIONS})"
    )
    op.drop_index("ix_entity_relations_object", table_name="entity_relations")
    op.execute("DROP INDEX uq_entity_relation")
    op.drop_table("entity_relations")
