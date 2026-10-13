"""The database itself holds a status to its known values and an article to one current row.

A typo in a status ("succeded") or a second current annotation used to be stored silently and then
read as nothing or as a double count (fix plan Β4).
"""

import ast
import asyncio
import importlib
import os
import re
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import CheckConstraint, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.session import session_factory
from app.feeds.models import Feed

pytestmark = [
    pytest.mark.skipif(
        os.getenv("NEWSINTEL_RUN_POSTGRES_TESTS") != "1",
        reason="requires the PostgreSQL fixture stack",
    ),
    pytest.mark.asyncio(loop_scope="session"),
]

APP = Path(__file__).resolve().parents[1] / "app"
for _models in sorted(APP.glob("*/models.py")):
    importlib.import_module(f"app.{_models.parent.name}.models")

# Every column that names a state or a kind out of a fixed set.
STATUS_COLUMNS = [
    ("nlp_jobs", "status"),
    ("nlp_processor_runs", "outcome"),
    ("article_nlp_states", "status"),
    ("nlp_reprocessing_runs", "status"),
    ("nlp_keywords", "kind"),
    ("nlp_entities", "entity_type"),
    ("article_country_annotations", "role"),
    ("article_processing_jobs", "status"),
    ("article_processing_jobs", "stage"),
    ("article_processing_jobs", "requested_mode"),
    ("article_processing_attempts", "status"),
    ("article_processing_attempts", "stage"),
    ("cluster_jobs", "status"),
    ("article_cluster_state", "status"),
    ("search_deliveries", "status"),
    ("search_rebuilds", "status"),
    ("search_index_targets", "role"),
    ("source_search_refreshes", "status"),
    ("feed_fetches", "status"),
    ("feeds", "fetching_mode"),
    ("maintenance_runs", "kind"),
    ("wikidata_throttle", "pause_reason"),
    ("wikidata_request_counts", "outcome"),
    ("wikidata_request_counts", "kind"),
]

# One current row per article and thing, as every reader of these tables assumes.
CURRENT_ROW_INDEXES = {
    "article_language_annotations": ("article_id",),
    "article_nlp_entities": ("article_id", "entity_id"),
    "article_nlp_keywords": ("article_id", "keyword_id"),
    "article_country_annotations": ("article_id", "role", "country_code"),
}

_ONE_OF = re.compile(r"^(?:\w+ IS NULL OR )?(\w+) IN \((.*)\)$")


def _model_values(table: str, column: str) -> tuple[str, ...]:
    """The values the model's CHECK on `column` allows, or () when it has none."""
    for constraint in Base.metadata.tables[table].constraints:
        if isinstance(constraint, CheckConstraint) and constraint.name == f"ck_{table}_{column}":
            match = _ONE_OF.match(str(constraint.sqltext))
            assert match and match.group(1) == column, constraint.sqltext
            return tuple(re.findall(r"'([^']*)'", match.group(2)))
    return ()


def _allowed_by_name() -> dict[str, set[str]]:
    """Each column name's values, over every CHECK in the models that holds one to a set."""
    allowed: dict[str, set[str]] = {}
    for table in Base.metadata.tables.values():
        for constraint in table.constraints:
            if isinstance(constraint, CheckConstraint):
                match = _ONE_OF.match(str(constraint.sqltext))
                if match:
                    allowed.setdefault(match.group(1), set()).update(
                        re.findall(r"'([^']*)'", match.group(2))
                    )
    return allowed


@pytest.mark.parametrize(("table", "column"), STATUS_COLUMNS)
async def test_an_unknown_status_is_rejected(table: str, column: str) -> None:
    values = _model_values(table, column)
    assert values, f"the model has no ck_{table}_{column}"
    async with session_factory() as db:
        expression = await db.scalar(
            text(
                "SELECT pg_get_expr(conbin, conrelid) FROM pg_constraint"
                " WHERE conrelid = CAST(:table AS regclass) AND conname = :name"
            ),
            {"table": table, "name": f"ck_{table}_{column}"},
        )
        assert expression is not None, f"the database has no ck_{table}_{column}"
        # Migration 0004 writes its own copy of the values; it must be the model's.
        assert sorted(re.findall(r"'([^']*)'", expression)) == sorted(values)
        # The constraint's own expression, evaluated against one value in place of the column.
        check = text(
            f"SELECT {expression} FROM (SELECT CAST(:value AS varchar) AS {column}) AS candidate"
        )
        for value in values:
            assert await db.scalar(check, {"value": value}) is True, value
        assert await db.scalar(check, {"value": "bogus"}) is False


async def test_a_bogus_fetching_mode_is_not_stored() -> None:
    async with session_factory() as db:
        db.add(
            Feed(
                name=f"Bogus {uuid.uuid4().hex[:8]}",
                url=f"http://127.0.0.1:1/{uuid.uuid4().hex}.xml",
                tags=[],
                poll_interval_minutes=30,
                fetching_mode="bogus",
            )
        )
        with pytest.raises(IntegrityError, match="ck_feeds_fetching_mode"):
            await db.flush()


# Literals under these names that are not a column's value.
NOT_A_COLUMN = {
    ("kind", "label"): "a Wikidata name's kind, held in memory (wikidata/names.py)",
    ("status", "sent"): "RunNow, the answer of 'run now' (processes/actions.py)",
}
_LOGGING = {"debug", "info", "warning", "error", "exception", "critical"}


def _literals(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
    return []


def _written(names: set[str]) -> list[tuple[str, str, str]]:
    """(column name, literal, where) for each literal the code writes to or compares with one."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(APP.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            where = f"{path.relative_to(APP.parent)}:{getattr(node, 'lineno', 0)}"
            pairs: list[tuple[str, ast.expr]] = []
            if isinstance(node, ast.Assign):
                pairs = [
                    (target.attr, node.value)
                    for target in node.targets
                    if isinstance(target, ast.Attribute)
                ]
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr in _LOGGING:
                    continue
                pairs = [(k.arg, k.value) for k in node.keywords if k.arg is not None]
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("in_", "not_in")
                    and isinstance(node.func.value, ast.Attribute)
                ):
                    pairs += [(node.func.value.attr, argument) for argument in node.args]
            elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute):
                pairs = [(node.left.attr, comparator) for comparator in node.comparators]
            found += [
                (name, literal, where)
                for name, value in pairs
                if name in names
                for literal in _literals(value)
            ]
    return found


def test_every_status_the_code_writes_is_allowed() -> None:
    allowed = _allowed_by_name()
    names = {column for _, column in STATUS_COLUMNS}
    written = _written(names)
    assert written
    unknown = sorted(
        f"{where}: {name} = {literal!r}"
        for name, literal, where in written
        if literal not in allowed.get(name, set()) and (name, literal) not in NOT_A_COLUMN
    )
    assert unknown == []


@pytest.mark.parametrize("table", sorted(CURRENT_ROW_INDEXES))
async def test_a_second_current_row_is_rejected(table: str) -> None:
    async with session_factory() as db:
        definition = await db.scalar(
            text("SELECT indexdef FROM pg_indexes WHERE tablename = :table AND indexname = :name"),
            {"table": table, "name": f"uq_{table}_current"},
        )
    columns = ", ".join(CURRENT_ROW_INDEXES[table])
    assert definition is not None, f"no uq_{table}_current"
    assert definition.startswith("CREATE UNIQUE INDEX")
    assert definition.endswith(f"({columns}) WHERE is_current")
    model_index = next(
        (i for i in Base.metadata.tables[table].indexes if i.name == f"uq_{table}_current"), None
    )
    assert model_index is not None and model_index.unique
    assert [column.name for column in model_index.columns] == list(CURRENT_ROW_INDEXES[table])


async def test_an_article_cannot_name_one_entity_twice_as_current() -> None:
    from tests.test_entity_authority_postgres import _article, _entity

    async with session_factory() as db:
        entity = await _entity(db, "Twice Named")
        with pytest.raises(IntegrityError, match="uq_article_nlp_entities_current"):
            await _article(db, [(entity, [0]), (entity, [10])])


async def _schema_facts() -> dict[str, object]:
    async with session_factory() as db:
        checks = await db.scalar(
            text(
                "SELECT count(*) FROM pg_constraint WHERE contype = 'c'"
                " AND conname = ANY(CAST(:names AS text[]))"
            ),
            {"names": [f"ck_{table}_{column}" for table, column in STATUS_COLUMNS]},
        )
        current = await db.scalar(
            text("SELECT count(*) FROM pg_indexes WHERE indexname = ANY(CAST(:names AS text[]))"),
            {"names": [f"uq_{table}_current" for table in CURRENT_ROW_INDEXES]},
        )
        tags = await db.scalar(
            text(
                "SELECT data_type FROM information_schema.columns"
                " WHERE table_name = 'feeds' AND column_name = 'tags'"
            )
        )
        duplicates = await db.scalar(
            text(
                "SELECT count(*) FROM pg_constraint"
                " WHERE conname IN ('users_username_key', 'sessions_token_hash_key')"
            )
        )
        sequences = await db.scalar(
            text(
                "SELECT count(*) FROM pg_class WHERE relkind = 'S' AND relname IN"
                " ('notification_settings_id_seq', 'wikidata_throttle_id_seq')"
            )
        )
    return {
        "checks": checks,
        "current": current,
        "tags": tags,
        "duplicates": duplicates,
        "sequences": sequences,
    }


async def test_the_integrity_rules_go_with_their_downgrade() -> None:
    applied = {
        "checks": len(STATUS_COLUMNS),
        "current": len(CURRENT_ROW_INDEXES),
        "tags": "jsonb",
        "duplicates": 0,
        "sequences": 0,
    }
    assert await _schema_facts() == applied
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "0003")
    try:
        assert await _schema_facts() == {
            "checks": 0,
            "current": 0,
            "tags": "json",
            "duplicates": 2,
            "sequences": 2,
        }
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    assert await _schema_facts() == applied


async def test_the_models_match_the_migrations() -> None:
    # Autogenerate finds nothing to add: types, defaults, indexes and unique constraints agree.
    await asyncio.to_thread(command.check, Config("alembic.ini"))
