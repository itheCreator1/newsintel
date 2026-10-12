"""Read, starred and a note per article.

Revision ID: 0027
Revises: 0026
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISCARD = "NEWSINTEL_MIGRATION_DISCARD_ARTICLE_MARKS"


def upgrade() -> None:
    op.create_table(
        "article_marks",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(note) <= 4000", name="ck_article_marks_note_length"),
    )
    op.create_index(
        "ix_article_marks_starred",
        "article_marks",
        ["article_id"],
        postgresql_where=sa.text("starred"),
    )


def downgrade() -> None:
    # Stars and notes are the user's own work: keep them unless told otherwise.
    kept = op.get_bind().execute(sa.text("SELECT count(*) FROM article_marks")).scalar_one()
    if kept and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "The database holds read marks, stars or notes in article_marks. Save them with"
            " `pg_dump --data-only --table=article_marks` first, then set "
            f"{DISCARD}=1 to downgrade and discard them."
        )
    op.drop_index("ix_article_marks_starred", table_name="article_marks")
    op.drop_table("article_marks")
