"""articles.enriched_at: when every processing step of an article had finished.

Watches count an article from then, not from its discovery (fix plan Γ3). An article whose
steps had all finished before this revision counts from its discovery, as it did until now;
the others get the date when their last step finishes.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("articles", sa.Column("enriched_at", sa.DateTime(timezone=True)))
    op.execute(
        """
        UPDATE articles SET enriched_at = first_discovered_at
        WHERE EXISTS (SELECT 1 FROM article_nlp_states s WHERE s.article_id = articles.id)
          AND NOT EXISTS (
            SELECT 1 FROM article_nlp_states s
            WHERE s.article_id = articles.id AND s.status IN ('queued', 'running', 'retrying'))
          AND NOT EXISTS (
            SELECT 1 FROM article_processing_jobs j
            WHERE j.article_id = articles.id AND j.status IN ('queued', 'running', 'retrying'))
        """
    )


def downgrade() -> None:
    op.drop_column("articles", "enriched_at")
