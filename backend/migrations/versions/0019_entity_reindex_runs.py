"""Authority runs may also be reindex runs: a rename refreshes the root's articles in batches.

Revision ID: 0019
Revises: 0018
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_entity_authority_run_kind", "entity_authority_runs", type_="check")
    op.create_check_constraint(
        "ck_entity_authority_run_kind",
        "entity_authority_runs",
        "kind IN ('merge', 'split', 'reindex')",
    )


def downgrade() -> None:
    # A reindex run only asks for search documents to be rebuilt; the articles and entities are
    # untouched, so dropping the unfinished ones loses no data. Rename again to refresh.
    op.execute("DELETE FROM entity_authority_runs WHERE kind = 'reindex'")
    op.drop_constraint("ck_entity_authority_run_kind", "entity_authority_runs", type_="check")
    op.create_check_constraint(
        "ck_entity_authority_run_kind", "entity_authority_runs", "kind IN ('merge', 'split')"
    )
