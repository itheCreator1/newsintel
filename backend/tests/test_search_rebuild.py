import re

from app.search.rebuild import new_index_name


def test_rebuild_indices_are_unique_and_schema_versioned() -> None:
    first = new_index_name(1)
    second = new_index_name(1)

    assert re.fullmatch(r"articles-v1-[0-9]{8}t[0-9]{12}z-[0-9a-f]{8}", first)
    assert first != second
