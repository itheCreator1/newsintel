import uuid

import pytest

from app.search.query import SearchSyntaxError, parse_query


def test_query_parser_combines_text_phrases_and_supported_fields() -> None:
    parsed = parse_query(
        'climate AND "sea level" source:Wire country:gr after:2026-01-01 before:2026-02-01'
    )

    assert parsed.terms == ["climate"]
    assert parsed.phrases == ["sea level"]
    assert parsed.source_values == ["Wire"]
    assert parsed.countries == ["GR"]
    assert parsed.after.isoformat() == "2026-01-01"
    assert parsed.before.isoformat() == "2026-02-01"


@pytest.mark.parametrize("value", ["title:test", '"unterminated', "after:yesterday", "AND news"])
def test_query_parser_rejects_actionable_invalid_syntax(value: str) -> None:
    with pytest.raises(SearchSyntaxError):
        parse_query(value)


def test_source_field_accepts_uuid() -> None:
    source_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    assert parse_query(f"source:{source_id}").source_values == [str(source_id)]
