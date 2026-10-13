"""feed_articles.metadata_json goes: the parser never filled it and nothing read it.

Revision ID: 0003
Revises: 0002
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("feed_articles", "metadata_json")


def downgrade() -> None:
    # Back as the empty object every row held, then without the default, as it was.
    op.add_column(
        "feed_articles",
        sa.Column("metadata_json", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )
    op.alter_column("feed_articles", "metadata_json", server_default=None)
