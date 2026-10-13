"""Whether a name is in use has one definition (fix plan Β5).

Four modules each had their own copy, and the NLP seed's left out the names an article was
observed under.
"""

import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
CALLERS = ["entities/authority.py", "entities/transfer.py", "wikidata/names.py", "nlp/authority.py"]


def test_only_the_entities_package_defines_in_use() -> None:
    definitions = [
        str(path.relative_to(APP))
        for path in sorted(APP.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and node.name in ("in_use", "_in_use")
    ]
    assert definitions == ["entities/in_use.py"]


@pytest.mark.parametrize("caller", CALLERS)
def test_each_caller_asks_the_shared_definition(caller: str) -> None:
    tree = ast.parse((APP / caller).read_text())
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "app.entities.in_use"
        for alias in node.names
    }
    assert imported == {"in_use"}
