"""How a Wikidata item is scored as a candidate for a root: every point has a stated reason."""

from app.wikidata.candidates import Scored, exact_choice, score_item
from app.wikidata.names import name_key
from app.wikidata.parsing import Item


def _names(entity_type: str, **by_language: list[str]) -> dict[str, set[str]]:
    return {
        language: {name_key(entity_type, language, text) for text in texts}
        for language, texts in by_language.items()
    }


def _item(qid: str = "Q1", **values: object) -> Item:
    return Item(qid=qid, state="ok", **values)  # type: ignore[arg-type]


def test_an_exact_label_in_the_roots_language_scores_most() -> None:
    names = _names("PERSON", en=["Alexis Tsipras"])
    item = _item(labels={"en": "Alexis Tsipras"})
    found = score_item(item, language="en", entity_type="PERSON", names=names, type_match=None)
    assert found == Scored("Q1", 0.6, ["label:en"], label_match=True, type_match=None)


def test_spelling_differences_ner_folds_still_match() -> None:
    names = _names("PERSON", el=["Αλέξης Τσίπρας"])
    item = _item(labels={"el": "ΑΛΕΞΗΣ ΤΣΙΠΡΑΣ"})
    found = score_item(item, language="el", entity_type="PERSON", names=names, type_match=None)
    assert found is not None and found.reasons == ["label:el"]


def test_an_alias_scores_less_than_a_label() -> None:
    names = _names("PERSON", en=["Tsipras"])
    item = _item(labels={"en": "Alexis Tsipras"}, aliases={"en": ["Tsipras"]})
    found = score_item(item, language="en", entity_type="PERSON", names=names, type_match=None)
    assert found is not None
    assert (found.score, found.reasons, found.label_match) == (0.4, ["alias:en"], False)


def test_the_type_adds_or_takes_away_and_known_items_win_ties() -> None:
    names = _names("GPE", en=["Georgia"])
    country = _item("Q230", labels={"en": "Georgia"}, sitelinks=400)
    state = _item("Q1428", labels={"en": "Georgia"}, sitelinks=20)
    matched = score_item(country, language="en", entity_type="GPE", names=names, type_match=True)
    differs = score_item(state, language="en", entity_type="GPE", names=names, type_match=False)
    assert matched == Scored("Q230", 0.9, ["label:en", "type_matches", "sitelinks:400"], True, True)
    assert differs is not None
    assert differs.reasons == ["label:en", "type_differs", "sitelinks:20"]
    assert 0.2 < differs.score < 0.3


def test_a_label_in_the_other_language_that_is_already_a_variant_adds() -> None:
    names = _names("PERSON", el=["Τσίπρας"], en=["Alexis Tsipras"])
    item = _item(
        labels={"el": "Αλέξης Τσίπρας", "en": "Alexis Tsipras"}, aliases={"el": ["Τσίπρας"]}
    )
    found = score_item(item, language="el", entity_type="PERSON", names=names, type_match=True)
    assert found is not None
    assert found.reasons == ["alias:el", "variant:en", "type_matches"]
    assert found.score == 0.8


def test_an_item_with_none_of_the_roots_names_is_no_candidate() -> None:
    names = _names("PERSON", en=["Alexis Tsipras"])
    item = _item(labels={"en": "Tsipras family"}, sitelinks=50)
    assert (
        score_item(item, language="en", entity_type="PERSON", names=names, type_match=True) is None
    )


def test_the_exact_choice_needs_one_exact_label_of_the_right_type() -> None:
    one = Scored("Q1", 0.9, ["label:en", "type_matches"], True, True)
    alias = Scored("Q2", 0.6, ["alias:en", "type_matches"], False, True)
    assert exact_choice([one, alias]) == "Q1"
    # Two items share the label ("Georgia"): the user picks, nothing is approved in bulk.
    other = Scored("Q3", 0.5, ["label:en", "type_differs"], True, False)
    assert exact_choice([one, other]) is None
    # One label match, but its type is unknown or wrong.
    assert exact_choice([Scored("Q1", 0.6, ["label:en"], True, None)]) is None
    assert exact_choice([other]) is None
    assert exact_choice([]) is None
