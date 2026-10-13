"""Indexes on the NLP tables' foreign keys, so deleting a job or run no longer scans its children.

Retention deleted 200 NLP jobs in 13.1 s without them and 5,000 in 0.24 s with them (database
review #1). The entity index covers past rows too, for merge, split and the in-use check.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FOREIGN_KEYS = [
    ("nlp_processor_runs", "job_id"),
    ("article_language_annotations", "run_id"),
    ("article_nlp_entities", "run_id"),
    ("article_nlp_keywords", "run_id"),
    ("article_nlp_keywords", "keyword_id"),
    ("article_country_annotations", "run_id"),
]


def upgrade() -> None:
    for table, column in FOREIGN_KEYS:
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.drop_index("ix_article_nlp_entities_entity_article", table_name="article_nlp_entities")
    op.create_index(
        "ix_article_nlp_entities_entity_article",
        "article_nlp_entities",
        ["entity_id", "article_id"],
    )
    op.create_index(
        "ix_article_nlp_entities_observed",
        "article_nlp_entities",
        ["observed_entity_id"],
        postgresql_where=sa.text("observed_entity_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_article_nlp_entities_observed", table_name="article_nlp_entities")
    op.drop_index("ix_article_nlp_entities_entity_article", table_name="article_nlp_entities")
    op.create_index(
        "ix_article_nlp_entities_entity_article",
        "article_nlp_entities",
        ["entity_id", "article_id"],
        postgresql_where=sa.text("is_current"),
    )
    for table, column in FOREIGN_KEYS:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
