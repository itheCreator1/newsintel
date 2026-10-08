"""A Wikidata refresh may cover every link, and a new link's fetch brings its labels.

Revision ID: 0023
Revises: 0022
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("force", "add_labels"):
        op.add_column(
            "wikidata_runs",
            sa.Column(name, sa.Boolean(), server_default="false", nullable=False),
        )


def downgrade() -> None:
    # Options of runs: a queued run without them refreshes what is due, the labels aside.
    op.drop_column("wikidata_runs", "add_labels")
    op.drop_column("wikidata_runs", "force")
