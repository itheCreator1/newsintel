"""Index each article's opening wording, so clustering can match rewordings of one story.

Revision ID: 0015
Revises: 0014
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Filled by the clusterer as it assigns articles; existing articles gain terms when reclustered.
    op.create_table(
        "article_cluster_terms",
        sa.Column(
            "article_id",
            sa.Uuid(),
            sa.ForeignKey("articles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("term", sa.String(64), primary_key=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_article_cluster_terms_term_date",
        "article_cluster_terms",
        ["term", "effective_at", "article_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_article_cluster_terms_term_date", table_name="article_cluster_terms")
    op.drop_table("article_cluster_terms")
