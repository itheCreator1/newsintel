"""Widen article_country_annotations.rule_version to fit real algorithm-version strings.

Revision ID: 0009
Revises: 0008

article_country_annotations.rule_version was declared String(64), but
detect_countries() in app.nlp.processors builds an algorithm-version string
from the checked-in country lexicon's "version" field
(f"country-explicit-{version}-primary-1"), which is 65 characters for the
lexicon in use today -- one over the limit. Every insert with at least one
detected country has been failing with StringDataRightTruncationError since
the column was introduced. NlpProcessorRun.algorithm_version already uses
String(128) for the exact same kind of value; this migration brings
rule_version in line with that precedent.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "article_country_annotations",
        "rule_version",
        existing_type=sa.String(64),
        type_=sa.String(128),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "article_country_annotations",
        "rule_version",
        existing_type=sa.String(128),
        type_=sa.String(64),
        existing_nullable=False,
    )
