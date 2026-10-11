"""A root can claim every type of one of its names, now and for names NER finds later.

Revision ID: 0026
Revises: 0025
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = (
    "'merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed',"
    " 'distinct_added', 'distinct_removed', 'relation_added', 'relation_changed',"
    " 'relation_removed', 'wikidata_linked', 'wikidata_unlinked', 'wikidata_redirected',"
    " 'wikidata_missing', 'type_changed', 'hidden_changed'"
)
CLAIM_ACTIONS = "'claim_added', 'claim_removed'"
DISCARD = "NEWSINTEL_MIGRATION_DISCARD_NAME_CLAIMS"


def _actions(value: str) -> None:
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", f"action IN ({value})"
    )


def upgrade() -> None:
    op.create_table(
        "entity_name_claims",
        sa.Column("language", sa.String(16), primary_key=True),
        sa.Column("normalized_text", sa.Text(), primary_key=True),
        sa.Column(
            "root_id",
            sa.Uuid(),
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_entity_name_claims_root_id", "entity_name_claims", ["root_id"])
    _actions(f"{ACTIONS}, {CLAIM_ACTIONS}")


def downgrade() -> None:
    # The claims are the user's decisions: keep them unless told otherwise. The merges a claim
    # made stay; only the rule for names NER finds later goes.
    bind = op.get_bind()
    kept = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM entity_name_claims) + (SELECT count(*)"
            f" FROM entity_authority_changes WHERE action IN ({CLAIM_ACTIONS}))"
        )
    ).scalar_one()
    if kept and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "The database holds name claims or their history. Save them with"
            " `authority export --file …` first, then set "
            f"{DISCARD}=1 to downgrade and discard them."
        )
    op.execute(f"DELETE FROM entity_authority_changes WHERE action IN ({CLAIM_ACTIONS})")
    _actions(ACTIONS)
    op.drop_index("ix_entity_name_claims_root_id", table_name="entity_name_claims")
    op.drop_table("entity_name_claims")
