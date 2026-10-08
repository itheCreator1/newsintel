"""Reading the Wikidata Action API's answers into the few fields the app keeps."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.wikidata.parsing import (
    Item,
    PageInfo,
    api_error,
    is_qid,
    parse_entities,
    parse_info,
    parse_search,
)

FIXTURES = Path(__file__).parent / "fixtures" / "wikidata"


def _load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _by_qid(items: list[Item]) -> dict[str, Item]:
    return {item.qid: item for item in items}


def test_an_item_keeps_names_in_our_languages_types_and_identifiers() -> None:
    items = _by_qid(parse_entities(_load("entities.json"), languages=("el", "en")))
    adams = items["Q42"]
    assert adams.state == "ok"
    assert adams.revision == 2215441093
    assert adams.labels == {"en": "Douglas Adams", "el": "Ντάγκλας Άνταμς"}
    assert adams.descriptions == {
        "en": "English writer and humorist (1952–2001)",
        "el": "Άγγλος συγγραφέας",
    }
    assert adams.aliases == {"en": ["Douglas Noel Adams", "Douglas N. Adams"]}
    assert adams.instance_of == ["Q5"]
    # A preferred statement wins over a normal one; a "no value" statement is no value.
    assert adams.ids == {"viaf": "113230702", "isni": "0000 0000 8045 6315", "lcnaf": "n80076765"}
    assert adams.different_from == ["Q9000001"]
    assert adams.sitelinks == 3
    assert adams.redirect_to is None


def test_names_in_other_languages_are_left_out() -> None:
    adams = _by_qid(parse_entities(_load("entities.json"), languages=("en",)))["Q42"]
    assert adams.labels == {"en": "Douglas Adams"}
    assert set(adams.descriptions) == {"en"}


def test_a_deprecated_statement_is_ignored() -> None:
    party = _by_qid(parse_entities(_load("entities.json"), languages=("el", "en")))["Q9000002"]
    assert party.instance_of == ["Q43229"]
    assert party.ids == {}
    assert party.sitelinks == 0


def test_a_redirect_names_its_target_and_the_target_is_read_too() -> None:
    items = _by_qid(parse_entities(_load("entities.json"), languages=("el", "en")))
    assert items["Q9000003"].state == "redirected"
    assert items["Q9000003"].redirect_to == "Q9000004"
    assert items["Q9000004"].state == "ok"
    assert items["Q9000004"].labels == {"en": "Merged Item"}


def test_a_missing_item_is_marked_missing() -> None:
    missing = _by_qid(parse_entities(_load("entities.json"), languages=("en",)))["Q9000005"]
    assert missing.state == "missing"
    assert missing.labels == {}
    assert missing.revision is None


def test_search_results_keep_their_order() -> None:
    assert parse_search(_load("search.json")) == ["Q42", "Q9000002"]
    assert parse_search(_load("search-empty.json")) == []


def test_page_info_gives_revisions_sizes_redirects_and_missing_items() -> None:
    pages = parse_info(_load("info.json"), requested=["Q42", "Q9000003", "Q9000005"])
    assert pages["Q42"] == PageInfo("Q42", 2215441093, 412345, "ok", None)
    assert pages["Q9000003"] == PageInfo("Q9000003", None, None, "redirected", "Q9000004")
    assert pages["Q9000004"] == PageInfo("Q9000004", 1800, 900, "ok", None)
    assert pages["Q9000005"] == PageInfo("Q9000005", None, None, "missing", None)


def test_an_api_error_is_read_in_either_error_format() -> None:
    assert api_error(_load("maxlag.json")) == (
        "maxlag",
        "Waiting for 10.64.48.35: 6 seconds lagged.",
    )
    assert api_error({"errors": [{"code": "ratelimited", "text": "slow down"}]}) == (
        "ratelimited",
        "slow down",
    )
    assert api_error(_load("search.json")) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Q42", True), ("Q1", True), ("q42", False), ("Q042", False), ("P31", False), ("Q", False)],
)
def test_a_qid_is_q_and_a_number(text: str, expected: bool) -> None:
    assert is_qid(text) is expected
