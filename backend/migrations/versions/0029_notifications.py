"""Push notifications through ntfy: one settings row, and per monitor whether and what it owes.

Revision ID: 0029
Revises: 0028
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DISCARD = "NEWSINTEL_MIGRATION_DISCARD_NOTIFICATIONS"


def upgrade() -> None:
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("server", sa.Text(), nullable=True),
        sa.Column("topic", sa.String(64), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("public_url", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(64), server_default="UTC", nullable=False),
        sa.Column("quiet_start", sa.Time(), nullable=True),
        sa.Column("quiet_end", sa.Time(), nullable=True),
        sa.Column("digest_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("digest_hour", sa.SmallInteger(), server_default="8", nullable=False),
        sa.Column("last_digest_on", sa.Date(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("id = 1", name="ck_notification_settings_single_row"),
        sa.CheckConstraint(
            "digest_hour BETWEEN 0 AND 23", name="ck_notification_settings_digest_hour"
        ),
        sa.CheckConstraint(
            "(quiet_start IS NULL) = (quiet_end IS NULL)", name="ck_notification_settings_quiet"
        ),
    )
    op.add_column(
        "monitors",
        sa.Column("notify", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.add_column(
        "monitors", sa.Column("notified_through", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "monitors",
        sa.Column(
            "pending_notification_articles", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_monitors_pending_notifications", "monitors", "pending_notification_articles >= 0"
    )
    op.create_index(
        "ix_monitors_pending_notifications",
        "monitors",
        ["id"],
        postgresql_where=sa.text("pending_notification_articles > 0"),
    )


def downgrade() -> None:
    # The ntfy address and the investigations muted on purpose are the user's settings.
    bind = op.get_bind()
    configured = bind.execute(sa.text("SELECT count(*) FROM notification_settings")).scalar_one()
    muted = bind.execute(sa.text("SELECT count(*) FROM monitors WHERE NOT notify")).scalar_one()
    if (configured or muted) and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "The database holds notification settings (notification_settings) or muted"
            " monitors (monitors.notify). Note them, or save them with `pg_dump --data-only"
            f" --table=notification_settings`, then set {DISCARD}=1 to downgrade and discard them."
        )
    op.drop_index("ix_monitors_pending_notifications", table_name="monitors")
    op.drop_constraint("ck_monitors_pending_notifications", "monitors", type_="check")
    op.drop_column("monitors", "pending_notification_articles")
    op.drop_column("monitors", "notified_through")
    op.drop_column("monitors", "notify")
    op.drop_table("notification_settings")
