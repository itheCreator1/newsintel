"""A monitor can watch a saved search in place, so the two show as one investigation.

Revision ID: 0028
Revises: 0027
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISCARD = "NEWSINTEL_MIGRATION_DISCARD_MONITOR_LINKS"


def upgrade() -> None:
    op.add_column(
        "monitors",
        sa.Column(
            "saved_search_id",
            sa.Uuid(),
            sa.ForeignKey(
                "saved_searches.id", ondelete="SET NULL", name="fk_monitors_saved_search"
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_monitors_saved_search",
        "monitors",
        ["saved_search_id"],
        unique=True,
        postgresql_where=sa.text("saved_search_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Which monitor watches which saved search is the user's choice: keep it unless told otherwise.
    kept = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM monitors WHERE saved_search_id IS NOT NULL"))
        .scalar_one()
    )
    if kept and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "Some monitors watch a saved search (monitors.saved_search_id). Save the links with"
            " `psql -c 'COPY (SELECT id, saved_search_id FROM monitors) TO STDOUT'` first, then"
            f" set {DISCARD}=1 to downgrade and discard them. The monitors and saved searches stay."
        )
    op.drop_index("uq_monitors_saved_search", table_name="monitors")
    op.drop_constraint("fk_monitors_saved_search", "monitors", type_="foreignkey")
    op.drop_column("monitors", "saved_search_id")
