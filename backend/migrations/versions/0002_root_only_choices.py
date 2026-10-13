"""A variant carries no type or "not a name" mark of its own: those are its root's.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Nothing showed or used a variant's own type or mark; merges now clear them.
    op.execute(
        "UPDATE nlp_entities SET preferred_type = NULL, hidden = false"
        " WHERE authority_id IS NOT NULL AND (preferred_type IS NOT NULL OR hidden)"
    )
    op.create_check_constraint(
        "ck_nlp_entity_root_only_choices",
        "nlp_entities",
        "authority_id IS NULL OR (preferred_type IS NULL AND NOT hidden)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_nlp_entity_root_only_choices", "nlp_entities", type_="check")
