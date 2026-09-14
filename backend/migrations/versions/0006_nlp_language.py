"""Add canonical detected-language annotations.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    uuid_type = postgresql.UUID()
    op.create_table(
        "article_language_annotations",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column(
            "article_id",
            uuid_type,
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            uuid_type,
            sa.ForeignKey("nlp_processor_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("margin", sa.Float(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index(
        "ix_article_language_current",
        "article_language_annotations",
        ["article_id", "is_current"],
    )


def downgrade() -> None:
    op.drop_table("article_language_annotations")
