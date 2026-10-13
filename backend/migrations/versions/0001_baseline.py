# ruff: noqa: E501  (generated: CHECK lists stay on one line, as the schema reads them)
"""Baseline: the whole schema in one migration.

Replaces the 31 migrations that built it; tests/test_migration_baseline_postgres.py checks that
this builds the same schema they did. A database made before the squash (at the old 0031) is
moved onto this with `alembic stamp 0001 --purge`.

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The trigram index on entity names needs the extension first.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.create_table(
        "articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("normalized_title_hash", sa.String(length=64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("normalized_url", name="uq_articles_normalized_url"),
    )
    op.create_index(
        "ix_articles_discovery", "articles", ["first_discovered_at", "id"], unique=False
    )
    op.create_index(
        "ix_articles_effective_date",
        "articles",
        [sa.literal_column("COALESCE(published_at, first_discovered_at)")],
        unique=False,
    )
    op.create_index("ix_articles_title_hash", "articles", ["normalized_title_hash"], unique=False)
    op.create_table(
        "entity_authority_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("other_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('merged', 'split', 'renamed', 'status_changed', 'ambiguous_changed', 'distinct_added', 'distinct_removed', 'relation_added', 'relation_changed', 'relation_removed', 'wikidata_linked', 'wikidata_unlinked', 'wikidata_redirected', 'wikidata_missing', 'type_changed', 'hidden_changed', 'claim_added', 'claim_removed')",
            name="ck_entity_authority_change_action",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_entity_authority_changes_entity",
        "entity_authority_changes",
        ["entity_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "event_association_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sweep", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("evaluated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created", sa.Integer(), server_default="0", nullable=False),
        sa.Column("deleted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_event_association_runs_started", "event_association_runs", ["started_at"], unique=False
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("primary_country", sa.String(length=2), nullable=True),
        sa.Column("cluster_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("article_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('active', 'closed', 'superseded')", name="ck_events_status"),
        sa.CheckConstraint("ended_at >= started_at", name="ck_events_span"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "algorithm_version", name="uq_events_id_version"),
    )
    op.create_index("ix_events_algorithm", "events", ["algorithm_version", "id"], unique=False)
    op.create_index(
        "ix_events_country_time", "events", ["primary_country", "ended_at", "id"], unique=False
    )
    op.create_index(
        "ix_events_size",
        "events",
        ["algorithm_version", "article_count", "ended_at", "id"],
        unique=False,
    )
    op.create_index("ix_events_status_time", "events", ["status", "ended_at", "id"], unique=False)
    op.create_index("ix_events_time", "events", ["ended_at", "id"], unique=False)
    op.create_table(
        "feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_country", sa.String(length=2), nullable=True),
        sa.Column("expected_language", sa.String(length=16), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("poll_interval_minutes", sa.Integer(), nullable=False),
        sa.Column("fetching_mode", sa.String(length=16), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column(
            "next_poll_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        "ix_feeds_due", "feeds", ["enabled", "retired_at", "next_poll_at"], unique=False
    )
    op.create_index(op.f("ix_feeds_retired_at"), "feeds", ["retired_at"], unique=False)
    op.create_table(
        "login_failures",
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("failures > 0", name="ck_login_failures_positive"),
        sa.PrimaryKeyConstraint("username"),
    )
    op.create_table(
        "maintenance_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_maintenance_runs_kind_started", "maintenance_runs", ["kind", "started_at"], unique=False
    )
    op.create_table(
        "nlp_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.Column("authority_id", sa.Uuid(), nullable=True),
        sa.Column("preferred_text", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'provisional'"), nullable=False
        ),
        sa.Column("ambiguous", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("preferred_type", sa.String(length=32), nullable=True),
        sa.Column("hidden", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "name_source", sa.String(length=16), server_default=sa.text("'ner'"), nullable=False
        ),
        sa.CheckConstraint(
            "name_source IN ('ner', 'seed', 'wikidata', 'user')", name="ck_nlp_entity_name_source"
        ),
        sa.CheckConstraint(
            "preferred_type IS NULL OR preferred_type IN ('PERSON', 'ORG', 'GPE', 'LOCATION', 'EVENT', 'PRODUCT', 'TOPIC', 'OTHER')",
            name="ck_nlp_entity_preferred_type",
        ),
        sa.CheckConstraint("status IN ('provisional', 'established')", name="ck_nlp_entity_status"),
        sa.CheckConstraint(
            "authority_id IS NULL OR authority_id <> id", name="ck_nlp_entity_not_own_authority"
        ),
        sa.ForeignKeyConstraint(["authority_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "language", "entity_type", "normalized_text", name="uq_nlp_entity_identity"
        ),
    )
    op.create_index(
        "ix_nlp_entities_ambiguous",
        "nlp_entities",
        ["language", "entity_type"],
        unique=False,
        postgresql_where=sa.text("ambiguous"),
    )
    op.create_index(
        "ix_nlp_entities_authority",
        "nlp_entities",
        ["authority_id"],
        unique=False,
        postgresql_where=sa.text("authority_id IS NOT NULL"),
    )
    op.create_index(
        "ix_nlp_entities_normalized_trgm",
        "nlp_entities",
        ["normalized_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"normalized_text": "gin_trgm_ops"},
    )
    op.create_table(
        "nlp_keywords",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("display_text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("language", "kind", "normalized_text", name="uq_nlp_keyword_identity"),
    )
    op.create_table(
        "nlp_language_settings",
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("ner_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("language"),
    )
    op.create_table(
        "nlp_reprocessing_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("processor_names", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("article_cursor", sa.Uuid(), nullable=True),
        sa.Column("scanned_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enqueued_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "nlp_stop_word_revisions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("words", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("language", "revision", name="uq_nlp_stop_word_revision"),
    )
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("server", sa.Text(), nullable=True),
        sa.Column("topic", sa.String(length=64), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("public_url", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(length=64), server_default="UTC", nullable=False),
        sa.Column("quiet_start", sa.Time(), nullable=True),
        sa.Column("quiet_end", sa.Time(), nullable=True),
        sa.Column("digest_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("digest_hour", sa.SmallInteger(), server_default="8", nullable=False),
        sa.Column("last_digest_on", sa.Date(), nullable=True),
        sa.Column("last_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(quiet_start IS NULL) = (quiet_end IS NULL)", name="ck_notification_settings_quiet"
        ),
        sa.CheckConstraint(
            "digest_hour BETWEEN 0 AND 23", name="ck_notification_settings_digest_hour"
        ),
        sa.CheckConstraint("id = 1", name="ck_notification_settings_single_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "search_index_targets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("index_name", sa.String(length=255), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("index_name"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=128), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username", name="users_username_key"),
    )
    op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)
    op.create_table(
        "wikidata_classes",
        sa.Column("qid", sa.String(length=16), nullable=False),
        sa.Column("label_en", sa.Text(), nullable=True),
        sa.Column(
            "subclass_of",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("qid"),
    )
    op.create_table(
        "wikidata_items",
        sa.Column("qid", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("redirect_to", sa.String(length=16), nullable=True),
        sa.Column("revision", sa.BigInteger(), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "aliases",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "descriptions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "instance_of",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "different_from",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column(
            "ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("sitelinks", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("claims_fetched", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "state IN ('ok', 'redirected', 'missing')", name="ck_wikidata_item_state"
        ),
        sa.PrimaryKeyConstraint("qid"),
    )
    op.create_table(
        "wikidata_request_counts",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("total_ms", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("day", "kind", "outcome"),
    )
    op.create_table(
        "wikidata_searches",
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("qids", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("language", "text"),
    )
    op.create_table(
        "wikidata_throttle",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("next_request_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pause_reason", sa.String(length=16), nullable=True),
        sa.Column("error_strikes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_rate_limited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("day", sa.Date(), nullable=True),
        sa.Column("requests_today", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_wikidata_throttle_one_row"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "article_cluster_state",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column(
            "requested_generation", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "completed_generation", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", name="uq_article_cluster_state"),
    )
    op.create_table(
        "article_cluster_terms",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("term", sa.String(length=64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id", "term"),
    )
    op.create_index(
        "ix_article_cluster_terms_term_date",
        "article_cluster_terms",
        ["term", "effective_at", "article_id"],
        unique=False,
    )
    op.create_table(
        "article_contents",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("previous_content_hash", sa.String(length=64), nullable=True),
        sa.Column("change_count", sa.Integer(), nullable=False),
        sa.Column("extractor_name", sa.String(length=64), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_content_change_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("html_object_key", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
        sa.UniqueConstraint("html_object_key"),
    )
    op.create_table(
        "article_marks",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("starred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(note) <= 4000", name="ck_article_marks_note_length"),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index(
        "ix_article_marks_starred",
        "article_marks",
        ["article_id"],
        unique=False,
        postgresql_where=sa.text("starred"),
    )
    op.create_table(
        "article_nlp_states",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("processor_name", sa.String(length=64), nullable=False),
        sa.Column(
            "requested_generation", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "completed_generation", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("processor_version", sa.String(length=64), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "processor_name", name="uq_article_nlp_state"),
    )
    op.create_table(
        "article_processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("requested_mode", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("temporary_html_key", sa.String(length=64), nullable=True),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
        sa.UniqueConstraint("temporary_html_key"),
    )
    op.create_index(
        "ix_article_jobs_article_created",
        "article_processing_jobs",
        ["article_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_article_jobs_completed", "article_processing_jobs", ["completed_at"], unique=False
    )
    op.create_index(
        "ix_article_jobs_due",
        "article_processing_jobs",
        ["status", "next_attempt_at", "claim_expires_at"],
        unique=False,
    )
    op.create_index(
        "uq_article_jobs_active",
        "article_processing_jobs",
        ["article_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running', 'retrying')"),
    )
    op.create_table(
        "article_search_states",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("requested_revision", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_table(
        "entity_authority_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'running'"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('merge', 'split', 'reindex')", name="ck_entity_authority_run_kind"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'finished', 'failed')", name="ck_entity_authority_run_status"
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entity_distinct",
        sa.Column("a_id", sa.Uuid(), nullable=False),
        sa.Column("b_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("a_id < b_id", name="ck_entity_distinct_ordered"),
        sa.ForeignKeyConstraint(["a_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["b_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("a_id", "b_id"),
    )
    op.create_table(
        "entity_external_ids",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("scheme", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default=sa.text("'user'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scheme <> 'wikidata' OR value ~ '^Q[1-9][0-9]*$'", name="ck_entity_external_id_qid"
        ),
        sa.CheckConstraint(
            "scheme IN ('wikidata', 'viaf', 'isni', 'lcnaf')", name="ck_entity_external_id_scheme"
        ),
        sa.CheckConstraint("source IN ('user', 'wikidata')", name="ck_entity_external_id_source"),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("entity_id", "scheme"),
    )
    op.create_index(
        "uq_entity_external_id_wikidata",
        "entity_external_ids",
        ["value"],
        unique=True,
        postgresql_where=sa.text("scheme = 'wikidata'"),
    )
    op.create_table(
        "entity_name_claims",
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("root_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["root_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("language", "normalized_text"),
    )
    op.create_index(
        op.f("ix_entity_name_claims_root_id"), "entity_name_claims", ["root_id"], unique=False
    )
    op.create_table(
        "entity_relations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("valid_from", sa.String(length=10), nullable=True),
        sa.Column("valid_to", sa.String(length=10), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source_article_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "relation_type <> 'related' OR subject_id < object_id",
            name="ck_entity_relation_related_ordered",
        ),
        sa.CheckConstraint(
            "relation_type IN ('succeeded_by', 'part_of', 'member_of', 'leader_of', 'related')",
            name="ck_entity_relation_type",
        ),
        sa.CheckConstraint(
            "valid_from IS NULL OR valid_from ~ '^\\d{4}(-\\d{2}(-\\d{2})?)?$'",
            name="ck_entity_relation_valid_from",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to ~ '^\\d{4}(-\\d{2}(-\\d{2})?)?$'",
            name="ck_entity_relation_valid_to",
        ),
        sa.CheckConstraint("subject_id <> object_id", name="ck_entity_relation_not_self"),
        sa.ForeignKeyConstraint(["object_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_article_id"], ["articles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["subject_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_entity_relations_object", "entity_relations", ["object_id"], unique=False)
    op.create_index(
        "uq_entity_relation",
        "entity_relations",
        ["subject_id", "relation_type", "object_id", sa.literal_column("coalesce(valid_from, '')")],
        unique=True,
    )
    op.create_table(
        "event_entities",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("article_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("article_count > 0", name="ck_event_entities_count"),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id", "entity_id"),
    )
    op.create_index(
        "ix_event_entities_entity", "event_entities", ["entity_id", "event_id"], unique=False
    )
    op.create_table(
        "feed_articles",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("guid", sa.Text(), nullable=True),
        sa.Column("feed_title", sa.Text(), nullable=False),
        sa.Column("feed_url", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "article_id", name="uq_feed_articles_feed_article"),
        sa.UniqueConstraint("feed_id", "guid", name="uq_feed_articles_feed_guid"),
    )
    op.create_index(
        "ix_feed_articles_article", "feed_articles", ["article_id", "discovered_at"], unique=False
    )
    op.create_table(
        "feed_fetches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("claim_token", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("invalid_entry_count", sa.Integer(), nullable=False),
        sa.Column("new_article_count", sa.Integer(), nullable=False),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        "ix_feed_fetches_history", "feed_fetches", ["feed_id", "started_at"], unique=False
    )
    op.create_index("ix_feed_fetches_started", "feed_fetches", ["started_at"], unique=False)
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_saved_searches_name_present"),
        sa.CheckConstraint("state_version > 0", name="ck_saved_searches_state_version"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_saved_searches_user_order", "saved_searches", ["user_id", "name", "id"], unique=False
    )
    op.create_index(
        "uq_saved_searches_user_name",
        "saved_searches",
        ["user_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "search_deliveries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("requested_revision", sa.Integer(), nullable=False),
        sa.Column("indexed_revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["search_index_targets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "target_id", name="uq_search_delivery_article_target"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        "ix_search_deliveries_due",
        "search_deliveries",
        ["status", "next_attempt_at", "claim_expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_search_deliveries_updated_at", "search_deliveries", ["updated_at"], unique=False
    )
    op.create_table(
        "search_rebuilds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("active_key", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("scan_cursor", sa.Uuid(), nullable=True),
        sa.Column("scanned_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("coordinator_token", sa.String(length=64), nullable=True),
        sa.Column("coordinator_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cutover_intent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["target_id"], ["search_index_targets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("coordinator_token"),
    )
    op.create_index("uq_search_rebuild_active", "search_rebuilds", ["active_key"], unique=True)
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="sessions_token_hash_key"),
    )
    op.create_index(op.f("ix_sessions_expires_at"), "sessions", ["expires_at"], unique=False)
    op.create_index(op.f("ix_sessions_token_hash"), "sessions", ["token_hash"], unique=True)
    op.create_table(
        "source_search_refreshes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("article_cursor", sa.Uuid(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["feed_id"], ["feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        "ix_source_search_refresh_due",
        "source_search_refreshes",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_table(
        "story_clusters",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("article_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("first_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("representative_article_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["representative_article_id"], ["articles.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_story_clusters_recency", "story_clusters", ["last_published_at", "id"], unique=False
    )
    op.create_table(
        "wikidata_candidates",
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("qid", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'::text[]"),
            nullable=False,
        ),
        sa.Column("dismissed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("exact", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("entity_id", "qid"),
    )
    op.create_index("ix_wikidata_candidates_qid", "wikidata_candidates", ["qid"], unique=False)
    op.create_index(
        "ix_wikidata_candidates_score",
        "wikidata_candidates",
        [sa.literal_column("score DESC")],
        unique=False,
    )
    op.create_table(
        "wikidata_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.Column("claim_token", sa.Uuid(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("force", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("add_labels", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("requests", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("checked", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("changed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("redirected", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("missing", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('candidates', 'refresh')", name="ck_wikidata_run_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'finished', 'failed', 'stopped')",
            name="ck_wikidata_run_status",
        ),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_wikidata_runs_status", "wikidata_runs", ["status", "created_at"], unique=False
    )
    op.create_table(
        "article_processing_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["job_id"], ["article_processing_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_attempts_job",
        "article_processing_attempts",
        ["job_id", "started_at"],
        unique=False,
    )
    op.create_table(
        "cluster_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["state_id"], ["article_cluster_state.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("article_id", "generation", name="uq_cluster_job_generation"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index("ix_cluster_jobs_completed", "cluster_jobs", ["completed_at"], unique=False)
    op.create_index(
        "ix_cluster_jobs_due",
        "cluster_jobs",
        ["status", "next_attempt_at", "claim_expires_at"],
        unique=False,
    )
    op.create_table(
        "event_clusters",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "signals",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "cluster_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["cluster_id"], ["story_clusters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["event_id", "algorithm_version"],
            ["events.id", "events.algorithm_version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("event_id", "cluster_id"),
        sa.UniqueConstraint(
            "algorithm_version", "cluster_id", name="uq_event_clusters_version_cluster"
        ),
    )
    op.create_index("ix_event_clusters_cluster", "event_clusters", ["cluster_id"], unique=False)
    op.create_table(
        "monitors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("saved_search_id", sa.Uuid(), nullable=True),
        sa.Column("eval_cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("eval_cursor_article_id", sa.Uuid(), nullable=True),
        sa.Column("viewed_cursor_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("viewed_cursor_article_id", sa.Uuid(), nullable=True),
        sa.Column("latest_match_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_match_article_id", sa.Uuid(), nullable=True),
        sa.Column("unseen_article_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unseen_cluster_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("notify", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "pending_notification_articles", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("notified_through", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "next_evaluation_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('search', 'entity', 'source', 'country', 'cluster', 'event')",
            name="ck_monitors_kind",
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_monitors_name_present"),
        sa.CheckConstraint(
            "pending_notification_articles >= 0", name="ck_monitors_pending_notifications"
        ),
        sa.CheckConstraint("state_version > 0", name="ck_monitors_state_version"),
        sa.CheckConstraint("unseen_article_count >= 0", name="ck_monitors_unseen_articles"),
        sa.CheckConstraint("unseen_cluster_count >= 0", name="ck_monitors_unseen_clusters"),
        sa.ForeignKeyConstraint(["latest_match_article_id"], ["articles.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["saved_search_id"],
            ["saved_searches.id"],
            name="fk_monitors_saved_search",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index(
        "ix_monitors_due",
        "monitors",
        ["next_evaluation_at", "id"],
        unique=False,
        postgresql_where=sa.text("enabled"),
    )
    op.create_index(
        "ix_monitors_pending_notifications",
        "monitors",
        ["id"],
        unique=False,
        postgresql_where=sa.text("pending_notification_articles > 0"),
    )
    op.create_index(
        "ix_monitors_user_activity", "monitors", ["user_id", "latest_match_at", "id"], unique=False
    )
    op.create_index("ix_monitors_user_order", "monitors", ["user_id", "name", "id"], unique=False)
    op.create_index(
        "uq_monitors_saved_search",
        "monitors",
        ["saved_search_id"],
        unique=True,
        postgresql_where=sa.text("saved_search_id IS NOT NULL"),
    )
    op.create_index(
        "uq_monitors_user_name",
        "monitors",
        ["user_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "nlp_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("state_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("processor_name", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("processor_version", sa.String(length=64), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claim_token", sa.String(length=64), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["state_id"], ["article_nlp_states.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "article_id", "processor_name", "generation", name="uq_nlp_job_generation"
        ),
        sa.UniqueConstraint("claim_token"),
    )
    op.create_index("ix_nlp_jobs_created_at", "nlp_jobs", ["created_at"], unique=False)
    op.create_index(
        "ix_nlp_jobs_due",
        "nlp_jobs",
        ["status", "next_attempt_at", "claim_expires_at"],
        unique=False,
    )
    op.create_table(
        "story_cluster_members",
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("cluster_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("algorithm_version", sa.String(length=64), nullable=False),
        sa.Column(
            "joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cluster_id"], ["story_clusters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("article_id"),
    )
    op.create_index(
        "ix_story_cluster_members_cluster",
        "story_cluster_members",
        ["cluster_id", "article_id"],
        unique=False,
    )
    op.create_table(
        "nlp_processor_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("processor_name", sa.String(length=64), nullable=False),
        sa.Column("processor_version", sa.String(length=64), nullable=False),
        sa.Column("algorithm_version", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("configuration_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("error_category", sa.String(length=64), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["nlp_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nlp_runs_article_processor",
        "nlp_processor_runs",
        ["article_id", "processor_name", "started_at"],
        unique=False,
    )
    op.create_index("ix_nlp_runs_completed", "nlp_processor_runs", ["completed_at"], unique=False)
    op.create_table(
        "article_country_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("role", sa.String(length=24), nullable=False),
        sa.Column("inferred", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("rule_version", sa.String(length=128), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("occurrences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["nlp_processor_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_country_current",
        "article_country_annotations",
        ["article_id", "role", "is_current"],
        unique=False,
    )
    op.create_table(
        "article_language_annotations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("margin", sa.Float(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["nlp_processor_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_language_current",
        "article_language_annotations",
        ["article_id", "is_current"],
        unique=False,
    )
    op.create_table(
        "article_nlp_entities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("observed_entity_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("original_label", sa.String(length=64), nullable=True),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("occurrences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["observed_entity_id"], ["nlp_entities.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["nlp_processor_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_nlp_entities_current",
        "article_nlp_entities",
        ["article_id", "is_current"],
        unique=False,
    )
    op.create_index(
        "ix_article_nlp_entities_entity_article",
        "article_nlp_entities",
        ["entity_id", "article_id"],
        unique=False,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "article_nlp_keywords",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("article_id", sa.Uuid(), nullable=False),
        sa.Column("keyword_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("occurrence_count", sa.Integer(), nullable=False),
        sa.Column("raw_score", sa.Float(), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("occurrences", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["article_id"], ["articles.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["keyword_id"], ["nlp_keywords.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["run_id"], ["nlp_processor_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_article_nlp_keywords_current",
        "article_nlp_keywords",
        ["article_id", "is_current"],
        unique=False,
    )
    # The throttle is one row, shared by every Wikidata request.
    op.execute("INSERT INTO wikidata_throttle (id) VALUES (1)")


def downgrade() -> None:
    op.drop_index("ix_article_nlp_keywords_current", table_name="article_nlp_keywords")
    op.drop_table("article_nlp_keywords")
    op.drop_index(
        "ix_article_nlp_entities_entity_article",
        table_name="article_nlp_entities",
        postgresql_where=sa.text("is_current"),
    )
    op.drop_index("ix_article_nlp_entities_current", table_name="article_nlp_entities")
    op.drop_table("article_nlp_entities")
    op.drop_index("ix_article_language_current", table_name="article_language_annotations")
    op.drop_table("article_language_annotations")
    op.drop_index("ix_article_country_current", table_name="article_country_annotations")
    op.drop_table("article_country_annotations")
    op.drop_index("ix_nlp_runs_completed", table_name="nlp_processor_runs")
    op.drop_index("ix_nlp_runs_article_processor", table_name="nlp_processor_runs")
    op.drop_table("nlp_processor_runs")
    op.drop_index("ix_story_cluster_members_cluster", table_name="story_cluster_members")
    op.drop_table("story_cluster_members")
    op.drop_index("ix_nlp_jobs_due", table_name="nlp_jobs")
    op.drop_index("ix_nlp_jobs_created_at", table_name="nlp_jobs")
    op.drop_table("nlp_jobs")
    op.drop_index("uq_monitors_user_name", table_name="monitors")
    op.drop_index(
        "uq_monitors_saved_search",
        table_name="monitors",
        postgresql_where=sa.text("saved_search_id IS NOT NULL"),
    )
    op.drop_index("ix_monitors_user_order", table_name="monitors")
    op.drop_index("ix_monitors_user_activity", table_name="monitors")
    op.drop_index(
        "ix_monitors_pending_notifications",
        table_name="monitors",
        postgresql_where=sa.text("pending_notification_articles > 0"),
    )
    op.drop_index("ix_monitors_due", table_name="monitors", postgresql_where=sa.text("enabled"))
    op.drop_table("monitors")
    op.drop_index("ix_event_clusters_cluster", table_name="event_clusters")
    op.drop_table("event_clusters")
    op.drop_index("ix_cluster_jobs_due", table_name="cluster_jobs")
    op.drop_index("ix_cluster_jobs_completed", table_name="cluster_jobs")
    op.drop_table("cluster_jobs")
    op.drop_index("ix_article_attempts_job", table_name="article_processing_attempts")
    op.drop_table("article_processing_attempts")
    op.drop_index("ix_wikidata_runs_status", table_name="wikidata_runs")
    op.drop_table("wikidata_runs")
    op.drop_index("ix_wikidata_candidates_score", table_name="wikidata_candidates")
    op.drop_index("ix_wikidata_candidates_qid", table_name="wikidata_candidates")
    op.drop_table("wikidata_candidates")
    op.drop_index("ix_story_clusters_recency", table_name="story_clusters")
    op.drop_table("story_clusters")
    op.drop_index("ix_source_search_refresh_due", table_name="source_search_refreshes")
    op.drop_table("source_search_refreshes")
    op.drop_index(op.f("ix_sessions_token_hash"), table_name="sessions")
    op.drop_index(op.f("ix_sessions_expires_at"), table_name="sessions")
    op.drop_table("sessions")
    op.drop_index("uq_search_rebuild_active", table_name="search_rebuilds")
    op.drop_table("search_rebuilds")
    op.drop_index("ix_search_deliveries_updated_at", table_name="search_deliveries")
    op.drop_index("ix_search_deliveries_due", table_name="search_deliveries")
    op.drop_table("search_deliveries")
    op.drop_index("uq_saved_searches_user_name", table_name="saved_searches")
    op.drop_index("ix_saved_searches_user_order", table_name="saved_searches")
    op.drop_table("saved_searches")
    op.drop_index("ix_feed_fetches_started", table_name="feed_fetches")
    op.drop_index("ix_feed_fetches_history", table_name="feed_fetches")
    op.drop_table("feed_fetches")
    op.drop_index("ix_feed_articles_article", table_name="feed_articles")
    op.drop_table("feed_articles")
    op.drop_index("ix_event_entities_entity", table_name="event_entities")
    op.drop_table("event_entities")
    op.drop_index("uq_entity_relation", table_name="entity_relations")
    op.drop_index("ix_entity_relations_object", table_name="entity_relations")
    op.drop_table("entity_relations")
    op.drop_index(op.f("ix_entity_name_claims_root_id"), table_name="entity_name_claims")
    op.drop_table("entity_name_claims")
    op.drop_index(
        "uq_entity_external_id_wikidata",
        table_name="entity_external_ids",
        postgresql_where=sa.text("scheme = 'wikidata'"),
    )
    op.drop_table("entity_external_ids")
    op.drop_table("entity_distinct")
    op.drop_table("entity_authority_runs")
    op.drop_table("article_search_states")
    op.drop_index(
        "uq_article_jobs_active",
        table_name="article_processing_jobs",
        postgresql_where=sa.text("status IN ('queued', 'running', 'retrying')"),
    )
    op.drop_index("ix_article_jobs_due", table_name="article_processing_jobs")
    op.drop_index("ix_article_jobs_completed", table_name="article_processing_jobs")
    op.drop_index("ix_article_jobs_article_created", table_name="article_processing_jobs")
    op.drop_table("article_processing_jobs")
    op.drop_table("article_nlp_states")
    op.drop_index(
        "ix_article_marks_starred", table_name="article_marks", postgresql_where=sa.text("starred")
    )
    op.drop_table("article_marks")
    op.drop_table("article_contents")
    op.drop_index("ix_article_cluster_terms_term_date", table_name="article_cluster_terms")
    op.drop_table("article_cluster_terms")
    op.drop_table("article_cluster_state")
    op.drop_table("wikidata_throttle")
    op.drop_table("wikidata_searches")
    op.drop_table("wikidata_request_counts")
    op.drop_table("wikidata_items")
    op.drop_table("wikidata_classes")
    op.drop_index(op.f("ix_users_username"), table_name="users")
    op.drop_table("users")
    op.drop_table("search_index_targets")
    op.drop_table("notification_settings")
    op.drop_table("nlp_stop_word_revisions")
    op.drop_table("nlp_reprocessing_runs")
    op.drop_table("nlp_language_settings")
    op.drop_table("nlp_keywords")
    op.drop_index(
        "ix_nlp_entities_normalized_trgm",
        table_name="nlp_entities",
        postgresql_using="gin",
        postgresql_ops={"normalized_text": "gin_trgm_ops"},
    )
    op.drop_index(
        "ix_nlp_entities_authority",
        table_name="nlp_entities",
        postgresql_where=sa.text("authority_id IS NOT NULL"),
    )
    op.drop_index(
        "ix_nlp_entities_ambiguous",
        table_name="nlp_entities",
        postgresql_where=sa.text("ambiguous"),
    )
    op.drop_table("nlp_entities")
    op.drop_index("ix_maintenance_runs_kind_started", table_name="maintenance_runs")
    op.drop_table("maintenance_runs")
    op.drop_table("login_failures")
    op.drop_index(op.f("ix_feeds_retired_at"), table_name="feeds")
    op.drop_index("ix_feeds_due", table_name="feeds")
    op.drop_table("feeds")
    op.drop_index("ix_events_time", table_name="events")
    op.drop_index("ix_events_status_time", table_name="events")
    op.drop_index("ix_events_size", table_name="events")
    op.drop_index("ix_events_country_time", table_name="events")
    op.drop_index("ix_events_algorithm", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_event_association_runs_started", table_name="event_association_runs")
    op.drop_table("event_association_runs")
    op.drop_index("ix_entity_authority_changes_entity", table_name="entity_authority_changes")
    op.drop_table("entity_authority_changes")
    op.drop_index("ix_articles_title_hash", table_name="articles")
    op.drop_index("ix_articles_effective_date", table_name="articles")
    op.drop_index("ix_articles_discovery", table_name="articles")
    op.drop_table("articles")
