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


def test_query_parser_supports_annotation_fields_without_changing_source_country() -> None:
    parsed = parse_query(
        'entity:"United Nations" keyword:energy language:en '
        "story_country:fr mentioned_country:de country:gr"
    )

    assert parsed.entity_values == ["United Nations"]
    assert parsed.keyword_values == ["energy"]
    assert parsed.languages == ["en"]
    assert parsed.story_countries == ["FR"]
    assert parsed.mentioned_countries == ["DE"]
    assert parsed.countries == ["GR"]
