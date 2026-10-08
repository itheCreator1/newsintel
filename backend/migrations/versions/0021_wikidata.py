"""Wikidata links for authority roots, the Wikidata cache and its shared throttle.

Revision ID: 0021
Revises: 0020
"""

import os
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = (
    "'merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed',"
    " 'distinct_added', 'distinct_removed', 'relation_added', 'relation_changed',"
    " 'relation_removed'"
)
WIKIDATA_ACTIONS = (
    "'wikidata_linked', 'wikidata_unlinked', 'wikidata_redirected', 'wikidata_missing'"
)
DISCARD = "NEWSINTEL_MIGRATION_DISCARD_WIKIDATA"


def _now() -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(
        "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.add_column(
        "nlp_entities",
        sa.Column("name_source", sa.String(16), server_default="ner", nullable=False),
    )
    op.create_check_constraint(
        "ck_nlp_entity_name_source",
        "nlp_entities",
        "name_source IN ('ner', 'seed', 'wikidata', 'user')",
    )
    # The country seed's spellings: English GPE variants no article has used, under their own
    # row or as a tagged mention in their root's rows.
    op.execute(
        """
        UPDATE nlp_entities AS e SET name_source = 'seed'
        WHERE e.language = 'en' AND e.entity_type = 'GPE' AND e.authority_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM article_nlp_entities a
            WHERE a.entity_id = e.id OR a.observed_entity_id = e.id
          )
          AND NOT EXISTS (
            SELECT 1 FROM article_nlp_entities a
            WHERE a.entity_id = e.authority_id
              AND a.occurrences::jsonb
                  @> jsonb_build_array(jsonb_build_object('entity_id', e.id::text))
          )
        """
    )

    op.create_table(
        "entity_external_ids",
        sa.Column(
            "entity_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("scheme", sa.String(16), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(16), server_default="user", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "scheme IN ('wikidata', 'viaf', 'isni', 'lcnaf')", name="ck_entity_external_id_scheme"
        ),
        sa.CheckConstraint("source IN ('user', 'wikidata')", name="ck_entity_external_id_source"),
        sa.CheckConstraint(
            "scheme <> 'wikidata' OR value ~ '^Q[1-9][0-9]*$'", name="ck_entity_external_id_qid"
        ),
    )
    # One QID on one root: a second root with the same QID is a merge, which the user decides.
    op.create_index(
        "uq_entity_external_id_wikidata",
        "entity_external_ids",
        ["value"],
        unique=True,
        postgresql_where=sa.text("scheme = 'wikidata'"),
    )

    state_check = "state IN ('ok', 'redirected', 'missing')"
    op.create_table(
        "wikidata_items",
        sa.Column("qid", sa.String(16), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("redirect_to", sa.String(16), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=True),
        sa.Column("labels", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("aliases", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("descriptions", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("instance_of", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "different_from", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("ids", postgresql.JSONB(), server_default="{}", nullable=False),
        sa.Column("sitelinks", sa.Integer(), server_default="0", nullable=False),
        sa.Column("claims_fetched", sa.Boolean(), server_default="false", nullable=False),
        _now(),
        sa.Column(
            "checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(state_check, name="ck_wikidata_item_state"),
    )
    op.create_table(
        "wikidata_classes",
        sa.Column("qid", sa.String(16), primary_key=True),
        sa.Column("label_en", sa.Text(), nullable=True),
        sa.Column("subclass_of", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        _now(),
    )
    op.create_table(
        "wikidata_searches",
        sa.Column("language", sa.String(16), primary_key=True),
        sa.Column("text", sa.Text(), primary_key=True),
        sa.Column("qids", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        _now(),
    )
    op.create_table(
        "wikidata_candidates",
        sa.Column(
            "entity_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("qid", sa.String(16), primary_key=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("reasons", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("dismissed", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_wikidata_candidates_qid", "wikidata_candidates", ["qid"])
    op.create_table(
        "wikidata_runs",
        sa.Column("id", postgresql.UUID(), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="queued", nullable=False),
        sa.Column(
            "entity_id",
            postgresql.UUID(),
            sa.ForeignKey("nlp_entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("cursor", sa.Text(), nullable=True),
        *(
            sa.Column(name, sa.Integer(), server_default="0", nullable=False)
            for name in ("requests", "checked", "changed", "redirected", "missing", "errors")
        ),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('candidates', 'refresh')", name="ck_wikidata_run_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed')", name="ck_wikidata_run_status"
        ),
    )
    op.create_index("ix_wikidata_runs_status", "wikidata_runs", ["status", "created_at"])
    op.create_table(
        "wikidata_throttle",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(16), nullable=True),
        sa.Column("error_strikes", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_rate_limited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("requests_today", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("id = 1", name="ck_wikidata_throttle_one_row"),
    )
    op.execute("INSERT INTO wikidata_throttle (id) VALUES (1)")
    op.create_table(
        "wikidata_request_counts",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("outcome", sa.String(16), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.BigInteger(), nullable=False),
    )
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action",
        "entity_authority_changes",
        f"action IN ({ACTIONS}, {WIKIDATA_ACTIONS})",
    )


def downgrade() -> None:
    # The links are the user's decisions: keep them unless told otherwise. The cache and the
    # throttle are copies and bookkeeping, and go.
    bind = op.get_bind()
    kept = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM entity_external_ids) + (SELECT count(*)"
            f" FROM entity_authority_changes WHERE action IN ({WIKIDATA_ACTIONS}))"
        )
    ).scalar_one()
    if kept and os.getenv(DISCARD) != "1":
        raise RuntimeError(
            "The database holds Wikidata links or their history. Save them with"
            " `authority export --file …` first, then set "
            f"{DISCARD}=1 to downgrade and discard them."
        )
    op.execute(f"DELETE FROM entity_authority_changes WHERE action IN ({WIKIDATA_ACTIONS})")
    op.drop_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", type_="check"
    )
    op.create_check_constraint(
        "ck_entity_authority_change_action", "entity_authority_changes", f"action IN ({ACTIONS})"
    )
    op.drop_table("wikidata_request_counts")
    op.drop_table("wikidata_throttle")
    op.drop_index("ix_wikidata_runs_status", table_name="wikidata_runs")
    op.drop_table("wikidata_runs")
    op.drop_index("ix_wikidata_candidates_qid", table_name="wikidata_candidates")
    op.drop_table("wikidata_candidates")
    op.drop_table("wikidata_searches")
    op.drop_table("wikidata_classes")
    op.drop_table("wikidata_items")
    op.drop_index("uq_entity_external_id_wikidata", table_name="entity_external_ids")
    op.drop_table("entity_external_ids")
    op.drop_constraint("ck_nlp_entity_name_source", "nlp_entities", type_="check")
    op.drop_column("nlp_entities", "name_source")
