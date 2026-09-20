import subprocess
import sys

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.events.models import STATUSES, Event, EventCluster, EventEntity


def _named(table, kind):  # type: ignore[no-untyped-def]
    return {c.name for c in table.constraints if isinstance(c, kind)}


def test_tables_and_statuses() -> None:
    assert (Event.__tablename__, EventCluster.__tablename__, EventEntity.__tablename__) == (
        "events",
        "event_clusters",
        "event_entities",
    )
    assert STATUSES == ("active", "closed", "superseded")


def test_events_are_versioned_and_span_checked() -> None:
    table = Event.__table__
    assert {"algorithm_version", "status", "started_at", "ended_at", "primary_country"} <= set(
        table.c.keys()
    )
    assert _named(table, UniqueConstraint) == {"uq_events_id_version"}
    assert {"ck_events_status", "ck_events_span"} <= _named(table, CheckConstraint)
    assert {i.name for i in table.indexes} == {
        "ix_events_status_time",
        "ix_events_country_time",
        "ix_events_time",
        "ix_events_algorithm",
    }


def test_a_cluster_belongs_to_one_event_per_version() -> None:
    table = EventCluster.__table__
    assert _named(table, UniqueConstraint) == {"uq_event_clusters_version_cluster"}
    pair = next(
        c
        for c in table.constraints
        if isinstance(c, ForeignKeyConstraint) and len(c.column_keys) == 2
    )
    assert set(pair.column_keys) == {"event_id", "algorithm_version"}
    assert {i.name for i in table.indexes} == {"ix_event_clusters_cluster"}


def test_entity_associations_are_indexed_both_ways() -> None:
    table = EventEntity.__table__
    assert [c.name for c in table.primary_key.columns] == ["event_id", "entity_id"]
    assert {i.name for i in table.indexes} == {"ix_event_entities_entity"}
    assert "ck_event_entities_count" in _named(table, CheckConstraint)


def test_models_resolve_in_a_fresh_interpreter() -> None:
    # The scheduler and worker will load events without the API's imports; this pytest process has
    # already imported every model, so only a fresh interpreter can see a missing one.
    code = (
        "import app.jobs.events  # the worker entrypoint\n"
        "from sqlalchemy.orm import configure_mappers; configure_mappers()\n"
        "from app.events.models import Event, EventCluster, EventEntity\n"
        "for t in (Event, EventCluster, EventEntity):\n"
        "    for key in t.__table__.foreign_keys: key.column\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-800:]
