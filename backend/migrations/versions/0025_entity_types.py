"""A root can carry the user's own type, and be hidden as not a name.

The model's type stays in entity_type: it is part of the identity NER finds a name by, so a
type the user picks goes in preferred_type beside it, as preferred_text does for the name.

Revision ID: 0025
Revises: 0024
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TYPES = ("PERSON", "ORG", "GPE", "LOCATION", "EVENT", "PRODUCT", "TOPIC", "OTHER")
ACTIONS = (
    "'merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed',"
    " 'distinct_added', 'distinct_removed', 'relation_added', 'relation_changed',"
    " 'relation_removed', 'wikidata_linked', 'wikidata_unlinked', 'wikidata_redirected',"
    " 'wikidata_missing'"
)
TYPE_ACTIONS = "'type_changed', 'hidden_changed'"
DISCARD = "NEWSINTEL_MIGRATION_DISCARD_ENTITY_TYPES"


def _actions(value: str) -> None:
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", f"action IN ({value})"
    )


def upgrade() -> None:
    op.add_column("nlp_entities", sa.Column("preferred_type", sa.String(32), nullable=True))
    op.add_column(
        "nlp_entities",
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    allowed = ", ".join(f"'{value}'" for value in TYPES)
    op.create_check_constraint(
        "ck_nlp_entity_preferred_type",
        "nlp_entities",
        f"preferred_type IS NULL OR preferred_type IN ({allowed})",
    )
    _actions(f"{ACTIONS}, {TYPE_ACTIONS}")


def downgrade() -> None:
    # The types and the hidden marks are the user's decisions: keep them unless told otherwise.
    bind = op.get_bind()
    kept = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM nlp_entities"
            " WHERE preferred_type IS NOT NULL OR hidden) + (SELECT count(*)"
            f" FROM entity_authority_changes WHERE action IN ({TYPE_ACTIONS}))"
        )
    ).scalar_one()
    if kept and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "The database holds entity types or hidden names the user chose, or their history."
            " Save them with `authority export --file …` first, then set "
            f"{DISCARD}=1 to downgrade and discard them."
        )
    op.execute(f"DELETE FROM entity_authority_changes WHERE action IN ({TYPE_ACTIONS})")
    _actions(ACTIONS)
    op.drop_constraint("ck_nlp_entity_preferred_type", "nlp_entities", type_="check")
    op.drop_column("nlp_entities", "hidden")
    op.drop_column("nlp_entities", "preferred_type")
