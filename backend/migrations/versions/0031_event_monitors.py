"""Monitors can watch an event: new articles in any story it holds.

Revision ID: 0031
Revises: 0030
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISCARD = "NEWSINTEL_MIGRATION_DISCARD_EVENT_MONITORS"
BEFORE = "kind IN ('search', 'entity', 'source', 'country', 'cluster')"
AFTER = "kind IN ('search', 'entity', 'source', 'country', 'cluster', 'event')"


def upgrade() -> None:
    op.drop_constraint("ck_monitors_kind", "monitors", type_="check")
    op.create_check_constraint("ck_monitors_kind", "monitors", AFTER)


def downgrade() -> None:
    watched = op.get_bind().scalar(sa.text("SELECT count(*) FROM monitors WHERE kind = 'event'"))
    if watched and os.environ.get(DISCARD) != "1":
        raise RuntimeError(
            f"{watched} event monitors would be deleted; set {DISCARD}=1 to downgrade anyway"
        )
    op.execute("DELETE FROM monitors WHERE kind = 'event'")
    op.drop_constraint("ck_monitors_kind", "monitors", type_="check")
    op.create_check_constraint("ck_monitors_kind", "monitors", BEFORE)
