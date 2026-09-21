"""Record each failed NLP processor run's error category on the run itself.

Revision ID: 0014
Revises: 0013
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("nlp_processor_runs", sa.Column("error_category", sa.String(64)))


def downgrade() -> None:
    op.drop_column("nlp_processor_runs", "error_category")
