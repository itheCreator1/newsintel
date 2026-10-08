"""Per-language NLP switches, so Greek entities can be turned on from Settings.

Revision ID: 0017
Revises: 0016
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "nlp_language_settings",
        sa.Column("language", sa.String(16), primary_key=True),
        sa.Column("ner_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("nlp_language_settings")
