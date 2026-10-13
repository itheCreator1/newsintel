"""The database holds statuses to their values, an article to one current row, and tags as jsonb.

Also drops what 0001 carried over from the old chain: users and sessions had a unique constraint
beside the unique index the models describe, and the two single-row tables had an id sequence
although their one id is always written.

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The values as of this revision; the models keep the living lists (test_integrity_postgres.py
# checks the two agree).
_QUEUE = ("queued", "running", "retrying", "succeeded", "failed")
_NLP_OUTCOMES = ("success", "unsupported_language", "disabled", "failed")
_WIKIDATA_OUTCOMES = ("ok", "rate_limited", "blocked", "error")
CHECKS: list[tuple[str, str, tuple[str, ...]]] = [
    ("nlp_jobs", "status", (*_QUEUE, "superseded")),
    ("nlp_processor_runs", "outcome", _NLP_OUTCOMES),
    ("article_nlp_states", "status", ("queued", "running", "retrying", *_NLP_OUTCOMES)),
    ("nlp_reprocessing_runs", "status", ("queued", "scanning", "succeeded", "stopped", "failed")),
    ("nlp_keywords", "kind", ("keyword", "keyphrase")),
    (
        "nlp_entities",
        "entity_type",
        ("PERSON", "ORG", "GPE", "LOCATION", "EVENT", "PRODUCT", "TOPIC", "OTHER"),
    ),
    ("article_country_annotations", "role", ("primary", "mentioned")),
    ("article_processing_jobs", "status", _QUEUE),
    ("article_processing_jobs", "stage", ("fetch", "extract")),
    ("article_processing_jobs", "requested_mode", ("full_text", "full_text_html")),
    ("article_processing_attempts", "status", ("running", "succeeded", "failed")),
    ("article_processing_attempts", "stage", ("fetch", "extract")),
    ("cluster_jobs", "status", (*_QUEUE, "superseded")),
    ("article_cluster_state", "status", _QUEUE),
    ("search_deliveries", "status", _QUEUE),
    ("search_rebuilds", "status", ("scanning", "catching_up", "completed")),
    ("search_index_targets", "role", ("current", "replacement", "retained")),
    ("source_search_refreshes", "status", _QUEUE),
    ("feed_fetches", "status", ("queued", "running", "success", "failed")),
    ("feeds", "fetching_mode", ("rss", "full_text", "full_text_html")),
    ("maintenance_runs", "kind", ("retention",)),
    ("wikidata_throttle", "pause_reason", _WIKIDATA_OUTCOMES[1:]),
    ("wikidata_request_counts", "outcome", _WIKIDATA_OUTCOMES),
    ("wikidata_request_counts", "kind", ("search", "info", "entities", "claims")),
]

CURRENT_ROWS = {
    "article_language_annotations": ["article_id"],
    "article_nlp_entities": ["article_id", "entity_id"],
    "article_nlp_keywords": ["article_id", "keyword_id"],
    "article_country_annotations": ["article_id", "role", "country_code"],
}

DUPLICATE_UNIQUES = {
    "users": ("users_username_key", "username"),
    "sessions": ("sessions_token_hash_key", "token_hash"),
}

SINGLE_ROW_TABLES = ("notification_settings", "wikidata_throttle")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    for table, column, values in CHECKS:
        op.create_check_constraint(f"ck_{table}_{column}", table, _one_of(column, values))
    for table, columns in CURRENT_ROWS.items():
        op.create_index(
            f"uq_{table}_current",
            table,
            columns,
            unique=True,
            postgresql_where=sa.text("is_current"),
        )
    op.alter_column(
        "feeds",
        "tags",
        type_=postgresql.JSONB(),
        postgresql_using="tags::jsonb",
    )
    for table, (name, _column) in DUPLICATE_UNIQUES.items():
        op.drop_constraint(name, table, type_="unique")
    for table in SINGLE_ROW_TABLES:
        op.alter_column(table, "id", server_default=None)
        op.execute(f"DROP SEQUENCE {table}_id_seq")


def downgrade() -> None:
    for table in SINGLE_ROW_TABLES:
        op.execute(f"CREATE SEQUENCE {table}_id_seq AS smallint OWNED BY {table}.id")
        op.alter_column(table, "id", server_default=sa.text(f"nextval('{table}_id_seq'::regclass)"))
    for table, (name, column) in DUPLICATE_UNIQUES.items():
        op.create_unique_constraint(name, table, [column])
    op.alter_column("feeds", "tags", type_=sa.JSON(), postgresql_using="tags::json")
    for table in CURRENT_ROWS:
        op.drop_index(f"uq_{table}_current", table_name=table)
    for table, column, _values in CHECKS:
        op.drop_constraint(f"ck_{table}_{column}", table, type_="check")
