"""Failed sign-ins per name, so repeated guesses hold the name for a while.

Revision ID: 0030
Revises: 0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "login_failures",
        sa.Column("username", sa.String(128), primary_key=True),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("failures > 0", name="ck_login_failures_positive"),
    )


def downgrade() -> None:
    # Counts of failed guesses, not the user's data: nothing to keep.
    op.drop_table("login_failures")
