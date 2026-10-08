"""Wikidata runs are claimed by one worker at a time; a candidate says if it is a clear match.

Revision ID: 0022
Revises: 0021
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("wikidata_runs", sa.Column("claim_token", postgresql.UUID(), nullable=True))
    op.add_column(
        "wikidata_runs", sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "wikidata_candidates",
        sa.Column("exact", sa.Boolean(), server_default="false", nullable=False),
    )
    op.create_index("ix_wikidata_candidates_score", "wikidata_candidates", [sa.text("score DESC")])


def downgrade() -> None:
    # A claim and the exact flag are bookkeeping, worked out again by the next run.
    op.drop_index("ix_wikidata_candidates_score", table_name="wikidata_candidates")
    op.drop_column("wikidata_candidates", "exact")
    op.drop_column("wikidata_runs", "claim_expires_at")
    op.drop_column("wikidata_runs", "claim_token")
