"""History cleanups are recorded, a Wikidata run can be stopped, and two job tables get the
indexes the Processes page and the hourly cleanup sort by.

Revision ID: 0024
Revises: 0023
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "maintenance_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", postgresql.JSONB(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
    )
    op.create_index(
        "ix_maintenance_runs_kind_started", "maintenance_runs", ["kind", "started_at"]
    )
    op.drop_constraint("ck_wikidata_run_status", "wikidata_runs", type_="check")
    op.create_check_constraint(
        "ck_wikidata_run_status",
        "wikidata_runs",
        "status IN ('queued', 'running', 'finished', 'failed', 'stopped')",
    )
    op.create_index("ix_nlp_jobs_created_at", "nlp_jobs", ["created_at"])
    op.create_index("ix_search_deliveries_updated_at", "search_deliveries", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_search_deliveries_updated_at", table_name="search_deliveries")
    op.drop_index("ix_nlp_jobs_created_at", table_name="nlp_jobs")
    # A stopped run is history, not a decision: it is kept, as a run that ended early.
    op.execute(
        "UPDATE wikidata_runs SET status = 'failed', error = 'Stopped by the user'"
        " WHERE status = 'stopped'"
    )
    op.drop_constraint("ck_wikidata_run_status", "wikidata_runs", type_="check")
    op.create_check_constraint(
        "ck_wikidata_run_status",
        "wikidata_runs",
        "status IN ('queued', 'running', 'finished', 'failed')",
    )
    op.drop_index("ix_maintenance_runs_kind_started", table_name="maintenance_runs")
    op.drop_table("maintenance_runs")
