"""Row ids are UUIDv7 (RFC 9562): time-ordered, so a big table's index grows at its end instead
of splitting pages at random (fix plan Β6). Tokens stay random (uuid4)."""

import importlib
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.core.ids import uuid7
from app.db.base import Base

APP = Path(__file__).resolve().parents[1] / "app"
for _models in sorted(APP.glob("*/models.py")):
    importlib.import_module(f"app.{_models.parent.name}.models")


def test_an_id_is_a_version_7_uuid_with_the_time_in_front() -> None:
    before = int(datetime.now(UTC).timestamp() * 1000)
    value = uuid7()
    after = int(datetime.now(UTC).timestamp() * 1000)
    assert isinstance(value, uuid.UUID)
    assert value.version == 7
    assert value.variant == uuid.RFC_4122
    assert before <= value.int >> 80 <= after


def test_ids_are_unique_and_in_the_order_they_were_made() -> None:
    values = [uuid7() for _ in range(20_000)]
    assert len(set(values)) == len(values)
    # Many share a millisecond; within one the counter still orders them.
    assert values == sorted(values)


def test_ids_made_on_several_threads_never_collide() -> None:
    made: list[list[uuid.UUID]] = [[] for _ in range(8)]

    def make(into: list[uuid.UUID]) -> None:
        into.extend(uuid7() for _ in range(5_000))

    threads = [threading.Thread(target=make, args=(into,)) for into in made]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    every = [value for values in made for value in values]
    assert len(set(every)) == len(every)
    assert all(values == sorted(values) for values in made)


def test_every_uuid_primary_key_defaults_to_uuid7() -> None:
    random_keys = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.tables.values()
        for column in table.primary_key.columns
        if column.default is not None
        and getattr(column.default, "arg", None) is not None
        and getattr(column.default.arg, "__wrapped__", column.default.arg) is not uuid7
        and isinstance(column.type.python_type, type)
        and column.type.python_type is uuid.UUID
    ]
    assert random_keys == []
