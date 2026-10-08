import os
import tomllib
from pathlib import Path

from app.nlp.input import InputSection
from app.nlp.processors import ProcessorContext, extract_entities


def test_pinned_spacy_model_extracts_real_english_entities() -> None:
    if os.getenv("NEWSINTEL_RUN_NER_TESTS") != "1":
        pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        ner = pyproject["dependency-groups"]["ner"]
        assert "spacy==3.8.16" in ner
        assert "en-core-web-sm==3.8.0" in ner
        return
    text = "Last week Barack Obama met Microsoft executives in London on Monday."
    result = extract_entities(
        ProcessorContext(
            text=text,
            input_fingerprint="c" * 64,
            sections=(InputSection("body", None, 0, len(text)),),
            language="en",
            stop_words=frozenset(),
            ner_enabled=True,
            ner_model="en_core_web_sm",
        )
    )

    assert result.outcome == "success"
    assert result.model_version == "3.8.0"
    assert ("barack obama", "PERSON") in {
        (entity.normalized_text, entity.entity_type) for entity in result.entities
    }
    assert ("microsoft", "ORG") in {
        (entity.normalized_text, entity.entity_type) for entity in result.entities
    }
    assert not {"last week", "monday"} & {entity.normalized_text for entity in result.entities}


def test_pinned_greek_model_extracts_real_greek_entities() -> None:
    if os.getenv("NEWSINTEL_RUN_NER_TESTS") != "1":
        pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        assert "el-core-news-sm==3.8.0" in pyproject["dependency-groups"]["ner"]
        return
    text = (
        "Ο πρωθυπουργός Κυριάκος Μητσοτάκης συναντήθηκε με τον Αλέξη Τσίπρα στην Αθήνα. "
        "Ο Τσίπρας δήλωσε ότι ο ΣΥΡΙΖΑ στηρίζει την Ελλάδα."
    )
    result = extract_entities(
        ProcessorContext(
            text=text,
            input_fingerprint="d" * 64,
            sections=(InputSection("body", None, 0, len(text)),),
            language="el",
            stop_words=frozenset(),
            ner_enabled=True,
            ner_model="en_core_web_sm",
            ner_model_el="el_core_news_sm",
        )
    )

    assert result.outcome == "success"
    assert result.algorithm_version == "spacy-ner-el-2"
    found = {(entity.normalized_text, entity.entity_type) for entity in result.entities}
    # "τον Αλέξη Τσίπρα" and "Ο Τσίπρας" are one person.
    assert ("αλεξη τσιπρα", "PERSON") in found
    assert ("τσιπρα", "PERSON") not in found
    assert {("κυριακο μητσοτακη", "PERSON"), ("αθηνα", "GPE"), ("συριζα", "ORG")} <= found


def test_pinned_greek_model_keeps_one_place_type_per_country() -> None:
    """el_core_news_sm tags countries GPE, so a Greek country is one entity, not GPE and LOCATION.

    Greek names skip the English country lexicon that folds LOC into GPE, so if a model update
    started tagging countries LOC, one country would split into two entities. This pins that it
    does not.
    """
    if os.getenv("NEWSINTEL_RUN_NER_TESTS") != "1":
        pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
        assert "el-core-news-sm==3.8.0" in pyproject["dependency-groups"]["ner"]
        return
    text = (
        "Στην Ελλάδα, ο πρωθυπουργός μίλησε για την Ελλάδα και την Τουρκία. "
        "Η κυβέρνηση της Ελλάδας αντέδρασε, ενώ η Γερμανία στήριξε την Ουκρανία."
    )
    result = extract_entities(
        ProcessorContext(
            text=text,
            input_fingerprint="e" * 64,
            sections=(InputSection("body", None, 0, len(text)),),
            language="el",
            stop_words=frozenset(),
            ner_enabled=True,
            ner_model="en_core_web_sm",
            ner_model_el="el_core_news_sm",
        )
    )

    assert result.outcome == "success"
    places = {
        entity.normalized_text: entity.entity_type
        for entity in result.entities
        if entity.entity_type in {"GPE", "LOCATION"}
    }
    types_of_greece = {
        entity.entity_type for entity in result.entities if entity.normalized_text == "ελλαδα"
    }
    assert types_of_greece == {"GPE"}
    assert {"ελλαδα", "τουρκια", "γερμανια", "ουκρανια"} <= {
        name for name, kind in places.items() if kind == "GPE"
    }
