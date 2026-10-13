import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.nlp.input import InputSection
from app.nlp.processors import (
    ConfigurationError,
    EntityValue,
    InputTooLarge,
    ProcessorContext,
    _is_junk_entity,
    canonical_entity,
    country_entity_names,
    detect_countries,
    detect_language,
    entity_lookup_prefixes,
    extract_entities,
    extract_keywords,
    greek_name_key,
    validate_input_size,
)

ENGLISH_TEXT = (
    "European officials discussed a new energy policy in Brussels after markets reacted. "
    "The policy includes renewable energy investment and stronger regional energy security."
)


def context(
    text: str,
    *,
    language: str = "en",
    ner_enabled: bool = False,
    ner_model_el: str | None = None,
) -> ProcessorContext:
    return ProcessorContext(
        text=text,
        input_fingerprint="a" * 64,
        sections=(InputSection("title", None, 0, text.find(".") + 1),),
        language=language,
        stop_words=frozenset({"after", "and", "the"}),
        ner_enabled=ner_enabled,
        ner_model="en_core_web_sm",
        ner_model_el=ner_model_el,
    )


def test_language_thresholds_return_und_for_short_or_ambiguous_text() -> None:
    assert detect_language(context("Short report.")).language == "und"
    assert detect_language(context("the la de et und och " * 12)).language == "und"


def test_oversized_input_fails_visibly_without_truncation() -> None:
    oversized = context("x" * 101)

    with pytest.raises(InputTooLarge, match="101 characters; maximum is 100"):
        validate_input_size(oversized, 100)


def test_language_detection_records_relative_confidence_and_version() -> None:
    result = detect_language(context(ENGLISH_TEXT * 2))

    assert result.outcome == "success"
    assert result.language == "en"
    assert result.confidence >= 0.80
    assert result.margin >= 0.20
    assert result.algorithm_version.startswith("lingua-")


def test_keywords_are_ranked_bounded_and_have_occurrence_references() -> None:
    result = extract_keywords(context(ENGLISH_TEXT))

    assert result.outcome == "success"
    assert 0 < len(result.keywords) <= 50
    assert all(1 <= len(keyword.normalized_text.split()) <= 3 for keyword in result.keywords)
    assert all(keyword.occurrence_count >= 1 for keyword in result.keywords)
    assert all(0 < keyword.relevance <= 1 for keyword in result.keywords)
    assert all(keyword.occurrences for keyword in result.keywords)
    assert "after" not in {keyword.normalized_text for keyword in result.keywords}
    assert [(-item.relevance, item.normalized_text) for item in result.keywords] == sorted(
        (-item.relevance, item.normalized_text) for item in result.keywords
    )


def test_non_english_annotation_processors_are_explicitly_unsupported() -> None:
    result = extract_keywords(context(ENGLISH_TEXT, language="fr"))

    assert result.outcome == "unsupported_language"
    assert result.keywords == ()


def test_ner_disabled_is_a_capability_outcome() -> None:
    result = extract_entities(context(ENGLISH_TEXT))

    assert result.outcome == "disabled"
    assert result.entities == ()


def test_enabled_ner_with_missing_model_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: None)

    with pytest.raises(ConfigurationError, match="spaCy is not installed"):
        extract_entities(context(ENGLISH_TEXT, ner_enabled=True))


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Last week", "DATE"),
        ("Monday", "DATE"),
        ("this morning", "TIME"),
        ("40%", "PERCENT"),
        ("$5 million", "MONEY"),
        ("two", "CARDINAL"),
        ("first", "ORDINAL"),
        ("10 km", "QUANTITY"),
        # Time phrases the model mislabels as named things.
        ("Last week", "ORG"),
        ("Tuesday", "PERSON"),
        ("the past two years", "EVENT"),
        ("Earlier this month", "ORG"),
        ("Yesterday", "GPE"),
        ("weeks ago", "ORG"),
        # Punctuation and fragments.
        ("'s", "ORG"),
        ("--", "PERSON"),
        ("U", "ORG"),
        ("40%", "ORG"),
        ("10", "GPE"),
    ],
)
def test_junk_entities_are_recognised(text: str, label: str) -> None:
    assert _is_junk_entity(text, label)


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("Barack Obama", "PERSON"),
        ("Microsoft", "ORG"),
        ("EU", "ORG"),
        ("Black Friday", "EVENT"),
        ("World War II", "EVENT"),
        ("Summer Olympics", "EVENT"),
        ("Europeans", "NORP"),
        ("the Week Magazine", "ORG"),
        # Short designations: one letter, but a digit makes them a name.
        ("B-1", "PRODUCT"),
        ("F1", "ORG"),
        ("Z-10", "PRODUCT"),
    ],
)
def test_named_entities_are_kept(text: str, label: str) -> None:
    assert not _is_junk_entity(text, label)


class _FakeSpan:
    def __init__(self, text: str, full_text: str, label: str) -> None:
        self.text = text
        self.label_ = label
        self.start_char = full_text.index(text)
        self.end_char = self.start_char + len(text)


def test_extract_entities_drops_junk_before_grouping(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Last week Microsoft said 40% of staff in London met on Monday."
    spans = [
        ("Last week", "ORG"),
        ("Microsoft", "ORG"),
        ("40%", "PERCENT"),
        ("London", "GPE"),
        ("Monday", "DATE"),
    ]

    class FakePipeline:
        meta = {"version": "test"}

        def __call__(self, value: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[_FakeSpan(span, value, label) for span, label in spans])

    fake_spacy = SimpleNamespace(load=lambda _, **__: FakePipeline())
    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.nlp.processors._import_spacy", lambda: fake_spacy)
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})

    result = extract_entities(context(text, ner_enabled=True))

    assert result.outcome == "success"
    assert {(entity.normalized_text, entity.entity_type) for entity in result.entities} == {
        ("microsoft", "ORG"),
        ("london", "GPE"),
    }


def _extract_with_spans(
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    spans: list[tuple[str, str]],
    *,
    language: str = "en",
) -> set[tuple[str, str, str, int]]:
    class FakePipeline:
        meta = {"version": "test"}

        def __call__(self, value: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[_FakeSpan(span, value, label) for span, label in spans])

    fake_spacy = SimpleNamespace(load=lambda _, **__: FakePipeline())
    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.nlp.processors._import_spacy", lambda: fake_spacy)
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})
    loaded: list[str] = []

    def load(model: str, **_: object) -> FakePipeline:
        loaded.append(model)
        return FakePipeline()

    fake_spacy.load = load
    result = extract_entities(
        context(text, language=language, ner_enabled=True, ner_model_el="el_core_news_sm")
    )
    assert loaded == [{"en": "en_core_web_sm", "el": "el_core_news_sm"}[language]]
    return {
        (entity.entity_type, entity.normalized_text, entity.text, entity.occurrence_count)
        for entity in result.entities
    }


@pytest.mark.parametrize(
    ("text", "label", "expected"),
    [
        ("U.S.", "GPE", ("GPE", "united states", "United States")),
        ("US", "GPE", ("GPE", "united states", "United States")),
        ("the United States", "GPE", ("GPE", "united states", "United States")),
        ("America", "GPE", ("GPE", "united states", "United States")),
        ("Britain", "GPE", ("GPE", "united kingdom", "United Kingdom")),
        ("Russian Federation", "GPE", ("GPE", "russia", "Russia")),
        ("Viet Nam", "LOC", ("GPE", "vietnam", "Vietnam")),
        # Only place labels are folded into countries: "US" as an organisation stays itself.
        ("US", "ORG", ("ORG", "us", "US")),
        # Ambiguous names are left alone, as the country matcher leaves them.
        ("Georgia", "GPE", ("GPE", "georgia", "Georgia")),
        ("The White House", "ORG", ("ORG", "white house", "The White House")),
        ("White House", "ORG", ("ORG", "white house", "White House")),
        ("Reuters'", "ORG", ("ORG", "reuters", "Reuters")),
        ("U.N.", "ORG", ("ORG", "un", "U.N.")),
        ("The Hague", "GPE", ("GPE", "hague", "The Hague")),
    ],
)
def test_canonical_entity_folds_spellings_of_one_name(
    text: str, label: str, expected: tuple[str, str, str]
) -> None:
    mapped = {"GPE": "GPE", "LOC": "LOCATION", "ORG": "ORG"}[label]
    assert canonical_entity(text, label, mapped) == expected


@pytest.mark.parametrize(
    ("text", "label"),
    [
        ("U.S.", "GPE"),
        ("US", "GPE"),
        ("the United States", "GPE"),
        ("America", "GPE"),
        ("Britain", "GPE"),
        ("Russian Federation", "GPE"),
        ("Viet Nam", "LOC"),
        ("US", "ORG"),
        ("Georgia", "GPE"),
        ("The White House", "ORG"),
        ("White House", "ORG"),
        ("Reuters'", "ORG"),
        ("U.N.", "ORG"),
        ("The Hague", "GPE"),
        ("Hague's", "GPE"),
    ],
)
def test_entity_name_keys_match_the_stored_identity(text: str, label: str) -> None:
    """A search for any spelling reaches the key extraction stored it under."""
    from app.nlp.processors import entity_name_keys

    mapped = {"GPE": "GPE", "LOC": "LOCATION", "ORG": "ORG"}[label]
    assert canonical_entity(text, label, mapped)[1] in entity_name_keys(text)


def test_entity_name_keys_keep_greek_keys() -> None:
    from app.nlp.processors import entity_name_keys

    assert "τσιπρα" in entity_name_keys("Τσίπρας")
    assert "τσιπρα" in entity_name_keys("ΤΣΙΠΡΑ")


def test_extract_entities_merges_spellings_of_one_country(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "The U.S. and the United States, or simply US, are one country."
    spans = [("U.S.", "GPE"), ("United States", "GPE"), ("US", "GPE")]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert entities == {("GPE", "united states", "United States", 3)}


def test_extract_entities_stores_one_row_when_a_country_is_both_gpe_and_loc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "US troops left. The U.S. said so."
    spans = [("US", "GPE"), ("U.S.", "LOC")]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert entities == {("GPE", "united states", "United States", 2)}


def test_extract_entities_merges_loc_and_fac_of_one_place(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Crowds filled Harbor Bridge. Police closed Harbor Bridge at noon."
    spans = [("Harbor Bridge", "LOC"), ("Harbor Bridge", "FAC")]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert entities == {("LOCATION", "harbor bridge", "Harbor Bridge", 2)}


def _extracted(
    monkeypatch: pytest.MonkeyPatch, text: str, spans: list[tuple[str, str]], language: str = "en"
) -> list[EntityValue]:
    class FakePipeline:
        meta = {"version": "test"}

        def __call__(self, value: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[_FakeSpan(span, value, label) for span, label in spans])

    fake_spacy = SimpleNamespace(load=lambda _, **__: FakePipeline())
    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.nlp.processors._import_spacy", lambda: fake_spacy)
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})
    result = extract_entities(
        context(text, language=language, ner_enabled=True, ner_model_el="el_core_news_sm")
    )
    return list(result.entities)


@pytest.mark.parametrize(
    ("spans", "expected"),
    [
        # Two of three mentions are FAC.
        ([("Harbor Bridge", "LOC"), ("Harbor Bridge", "FAC"), ("Harbor Bridge", "FAC")], "FAC"),
        ([("Harbor Bridge", "LOC"), ("Harbor Bridge", "LOC"), ("Harbor Bridge", "FAC")], "LOC"),
        # A tie goes to the label first in the alphabet.
        ([("Harbor Bridge", "LOC"), ("Harbor Bridge", "FAC")], "FAC"),
    ],
)
def test_extract_entities_keeps_the_commonest_original_label(
    monkeypatch: pytest.MonkeyPatch, spans: list[tuple[str, str]], expected: str
) -> None:
    entities = _extracted(monkeypatch, "They met on Harbor Bridge.", spans)

    assert [(entity.original_label, entity.occurrence_count) for entity in entities] == [
        (expected, len(spans))
    ]


@pytest.mark.parametrize(
    ("language", "expected"), [("en", "spacy-ner-map-4"), ("el", "spacy-ner-el-2")]
)
def test_entity_algorithm_version_marks_the_one_row_per_entity_rule(
    monkeypatch: pytest.MonkeyPatch, language: str, expected: str
) -> None:
    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})
    monkeypatch.setattr(
        "app.nlp.processors._import_spacy",
        lambda: SimpleNamespace(load=lambda *_, **__: _NoEntities()),
    )

    result = extract_entities(
        context("Text.", language=language, ner_enabled=True, ner_model_el="el_core_news_sm")
    )

    assert result.algorithm_version == expected


class _NoEntities:
    meta = {"version": "test"}

    def __call__(self, _: str) -> SimpleNamespace:
        return SimpleNamespace(ents=[])


def test_extract_entities_merges_greek_rows_with_one_key(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Πορεία προς την Ακρόπολη. Ο χώρος της Ακρόπολης έκλεισε."
    spans = [("Ακρόπολη", "LOC"), ("Ακρόπολης", "FAC")]

    entities = _extract_with_spans(monkeypatch, text, spans, language="el")

    # Which form is shown is the nominative rule's business; here only the identity counts.
    assert {(kind, key, count) for kind, key, _, count in entities} == {("LOCATION", "ακροπολη", 2)}


def test_extract_entities_orders_merged_occurrences_by_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "The U.S. said so. US troops left."
    entities = _extracted(monkeypatch, text, [("US", "GPE"), ("U.S.", "LOC")])

    assert len(entities) == 1
    starts = [item.input_start for item in entities[0].occurrences]
    assert starts == sorted(starts)


def test_extract_entities_folds_a_surname_into_the_one_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Donald Trump spoke. Trump later left. Joe Biden replied; Biden smiled."
    spans = [
        ("Donald Trump", "PERSON"),
        ("Trump", "PERSON"),
        ("Joe Biden", "PERSON"),
        ("Biden", "PERSON"),
    ]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert entities == {
        ("PERSON", "donald trump", "Donald Trump", 2),
        ("PERSON", "joe biden", "Joe Biden", 2),
    }


def test_merge_short_person_names_skips_ambiguous_names(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Donald Trump spoke. Trump later left."
    spans = [("Donald Trump", "PERSON"), ("Trump", "PERSON")]

    class FakePipeline:
        meta = {"version": "test"}

        def __call__(self, value: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[_FakeSpan(span, value, label) for span, label in spans])

    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr(
        "app.nlp.processors._import_spacy",
        lambda: SimpleNamespace(load=lambda *_, **__: FakePipeline()),
    )
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})
    ambiguous = replace(context(text, ner_enabled=True), ambiguous_names=frozenset({"trump"}))

    result = extract_entities(ambiguous)

    # The user marked "Trump" ambiguous, so the surname alone is not folded into a full name.
    assert {entity.normalized_text for entity in result.entities} == {"donald trump", "trump"}


def test_extract_entities_keeps_a_surname_shared_by_two_people(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = "Donald Trump and Melania Trump arrived. Trump waved."
    spans = [("Donald Trump", "PERSON"), ("Melania Trump", "PERSON"), ("Trump", "PERSON")]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert {normalized for _, normalized, _, _ in entities} == {
        "donald trump",
        "melania trump",
        "trump",
    }


def test_country_matching_is_explicit_and_primary_rule_requires_title_and_body() -> None:
    text = (
        "France announces energy policy\n\n"
        "Officials in France described the policy. Germany replied."
    )
    result = detect_countries(
        ProcessorContext(
            text=text,
            input_fingerprint="b" * 64,
            sections=(
                InputSection("title", None, 0, 30),
                InputSection("body", None, 32, len(text)),
            ),
            language="en",
            stop_words=frozenset(),
            ner_enabled=False,
            ner_model="en_core_web_sm",
        )
    )

    assert {item.country_code for item in result.mentioned} == {"FR", "DE"}
    assert result.primary is not None and result.primary.country_code == "FR"
    assert result.primary.inferred is True
    # Must fit ArticleCountryAnnotation.rule_version (String(128), matching
    # NlpProcessorRun.algorithm_version's precedent) or every DB insert fails.
    assert len(result.algorithm_version) <= 128


def test_ambiguous_country_aliases_are_excluded() -> None:
    result = detect_countries(context("Georgia tells us a story about policy and elections. " * 3))

    assert result.mentioned == ()
    assert result.primary is None


def test_country_lexicon_is_checked_in_with_attribution() -> None:
    lexicon = Path(__file__).parents[1] / "app" / "nlp" / "data" / "countries-en.json"

    assert lexicon.exists()
    assert "ISO 3166-1" in lexicon.read_text()
    assert '"code": "ZW"' in lexicon.read_text()


def test_enabled_ner_loads_each_model_once_without_unused_components(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[tuple[str, list[str]]] = []

    class Pipeline:
        meta = {"version": "test"}

        def __call__(self, text: str) -> SimpleNamespace:
            return SimpleNamespace(ents=[])

    def load(model: str, *, exclude: list[str]) -> Pipeline:
        loads.append((model, exclude))
        return Pipeline()

    monkeypatch.setattr("app.nlp.processors.importlib.util.find_spec", lambda _: object())
    monkeypatch.setattr("app.nlp.processors._import_spacy", lambda: SimpleNamespace(load=load))
    monkeypatch.setattr("app.nlp.processors._ner_pipelines", {})

    for _ in range(3):
        assert extract_entities(context(ENGLISH_TEXT, ner_enabled=True)).outcome == "success"

    assert len(loads) == 1
    model, exclude = loads[0]
    assert model == "en_core_web_sm"
    assert {"tagger", "lemmatizer"} <= set(exclude)
    # The parser stays: NER never lets an entity cross a sentence boundary the parser set.
    assert not {"ner", "tok2vec", "parser", "senter"} & set(exclude)


def test_country_entity_names_follow_the_folded_spellings() -> None:
    assert "united states" in country_entity_names("U.S.")
    assert "united states" in country_entity_names("the US")
    assert "united kingdom" in country_entity_names("uk")
    # Still being typed: the closing dot is what makes "U.S." an acronym.
    assert "united states" in country_entity_names("u.s")
    assert "united states" in country_entity_names("the U.S.A")
    assert country_entity_names("u.s. n") == frozenset()
    assert country_entity_names("zzz") == frozenset()
    assert country_entity_names("  ") == frozenset()


def test_greek_ner_is_off_until_a_greek_model_is_named() -> None:
    text = "Ο Αλέξης Τσίπρας μίλησε στην Αθήνα."

    result = extract_entities(context(text, language="el", ner_enabled=True))

    assert result.outcome == "unsupported_language"
    assert result.algorithm_version == "spacy-ner-el-2"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Τσίπρας", "τσιπρα"),
        ("ΤΣΙΠΡΑΣ", "τσιπρα"),
        ("Τσιπρας", "τσιπρα"),
        ("Τσίπρα", "τσιπρα"),
        ("Αλέξη Τσίπρα", "αλεξη τσιπρα"),
        ("Παπαδόπουλος", "παπαδοπουλο"),
        ("Παπαδόπουλου", "παπαδοπουλο"),
        ("Ελλάδας", "ελλαδα"),
        ("Ευρωπαϊκής Ένωσης", "ευρωπαικη ενωση"),
        ("ΣΥΡΙΖΑ", "συριζα"),
        # Latin words in a Greek article keep their endings.
        ("Google", "google"),
        ("News", "news"),
    ],
)
def test_greek_name_key_folds_accents_capitals_and_case_endings(text: str, expected: str) -> None:
    assert greek_name_key(text) == expected


def test_canonical_entity_keeps_the_greek_spelling_for_display() -> None:
    assert canonical_entity("Τσίπρα", "PERSON", "PERSON", "el") == ("PERSON", "τσιπρα", "Τσίπρα")
    # English names are folded exactly as before.
    assert canonical_entity("U.S.", "GPE", "GPE") == ("GPE", "united states", "United States")


def test_extract_entities_folds_inflected_greek_names(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "Συνάντηση με τον Αλέξη Τσίπρα. Ο Αλέξης Τσίπρας είπε. Ο Τσίπρας έφυγε από την Αθήνα."
    spans = [
        ("Αλέξη Τσίπρα", "PERSON"),
        ("Αλέξης Τσίπρας", "PERSON"),
        ("Τσίπρας", "PERSON"),
        ("Αθήνα", "GPE"),
    ]

    entities = _extract_with_spans(monkeypatch, text, spans, language="el")

    # One person, shown in the subject case even though the object case came first.
    assert entities == {
        ("PERSON", "αλεξη τσιπρα", "Αλέξης Τσίπρας", 3),
        ("GPE", "αθηνα", "Αθήνα", 1),
    }


def test_entity_lookup_prefixes_match_greek_names_as_typed() -> None:
    assert entity_lookup_prefixes("Τσίπρας") == {"τσιπρα", "τσιπρασ"}
    # Mid-word, the accent-free spelling keeps matching "κουτσουμπα".
    assert "κουτσου" in entity_lookup_prefixes("Κουτσού")
    assert entity_lookup_prefixes(" Barack ") == {"barack"}


def test_greek_switch_changes_the_entity_fingerprint_only_when_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.nlp import service

    monkeypatch.setattr(service, "get_settings", lambda: Settings(nlp_ner_enabled=True))

    def fingerprint(greek_ner: bool) -> dict[str, str]:
        return {
            name: service.configuration_fingerprint(name, greek_ner=greek_ner)
            for name in service.PROCESSORS
        }

    expected = hashlib.sha256(
        json.dumps(
            {
                "processor": "entities",
                "version": "1",
                "stop_words": None,
                "ner_enabled": True,
                "ner_model": "en_core_web_sm",
                "max_input_characters": Settings().nlp_max_input_characters,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    without = fingerprint(False)
    with_greek = fingerprint(True)

    # An install that leaves Greek off keeps the fingerprints its articles were processed with.
    assert without["entities"] == expected
    assert with_greek["entities"] != expected
    assert {k: v for k, v in with_greek.items() if k != "entities"} == {
        k: v for k, v in without.items() if k != "entities"
    }


GREEK_TEXT = (
    "Η κυβέρνηση ανακοίνωσε νέο ενεργειακό σχέδιο για την Αθήνα. "
    "Το ενεργειακό σχέδιο περιλαμβάνει επενδύσεις στις ανανεώσιμες πηγές ενέργειας "
    "και στην ενεργειακή ασφάλεια της χώρας, μετά τις αντιδράσεις των αγορών."
)
GREEK_STOP_WORDS = frozenset({"η", "το", "την", "της", "για", "και", "στις", "στην", "των", "μετά"})


def greek_context(text: str, title_end: int | None = None) -> ProcessorContext:
    end = text.find(".") + 1 if title_end is None else title_end
    return ProcessorContext(
        text=text,
        input_fingerprint="c" * 64,
        sections=(
            InputSection("title", None, 0, end),
            InputSection("body", None, end + 1, len(text)),
        ),
        language="el",
        stop_words=GREEK_STOP_WORDS,
        ner_enabled=False,
        ner_model="en_core_web_sm",
    )


def test_greek_articles_get_keywords_without_stop_words() -> None:
    result = extract_keywords(greek_context(GREEK_TEXT))

    assert result.outcome == "success"
    assert 0 < len(result.keywords) <= 50
    normalized = {keyword.normalized_text for keyword in result.keywords}
    assert normalized.isdisjoint(GREEK_STOP_WORDS)
    assert all(keyword.occurrences for keyword in result.keywords)
    # Greek runs carry their own algorithm version, so English runs keep theirs.
    assert result.algorithm_version != extract_keywords(context(ENGLISH_TEXT)).algorithm_version
    assert len(result.algorithm_version) <= 128


def test_greek_keywords_fold_accents_and_capitals_into_one_key() -> None:
    text = "ΕΝΕΡΓΕΙΑΚΟ ΣΧΕΔΙΟ ΓΙΑ ΤΗΝ ΑΘΗΝΑ. " + GREEK_TEXT

    result = extract_keywords(greek_context(text))

    keys = [keyword.normalized_text for keyword in result.keywords]
    assert len(keys) == len(set(keys))
    # "ενεργειακό σχέδιο" and "ΕΝΕΡΓΕΙΑΚΟ ΣΧΕΔΙΟ" are one keyword, stored without accents.
    assert all(key == key.casefold() and "ό" not in key and "έ" not in key for key in keys)
    assert any("σχεδιο" in key for key in keys)


def test_greek_stop_words_match_unaccented_capitals() -> None:
    # Greek headlines are often written in capitals without accents: "ΑΠΟ" is the stop word "από".
    text = (
        "ΜΕΤΡΑ ΑΠΟ ΤΗΝ ΚΥΒΕΡΝΗΣΗ. Μέτρα από την κυβέρνηση για την ενέργεια και τις τιμές ρεύματος."
    )
    context_value = replace(
        greek_context(text), stop_words=frozenset({"από", "την", "για", "και", "τις"})
    )

    result = extract_keywords(context_value)

    assert result.outcome == "success" and result.keywords
    assert "απο" not in {keyword.normalized_text for keyword in result.keywords}
    assert all("απο" not in keyword.normalized_text.split() for keyword in result.keywords)


def test_annotation_processors_other_than_english_and_greek_stay_unsupported() -> None:
    assert extract_keywords(context(ENGLISH_TEXT, language="fr")).outcome == "unsupported_language"
    assert detect_countries(context(ENGLISH_TEXT, language="fr")).outcome == "unsupported_language"


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        # Nominative, genitive and accusative of the same country.
        ("Η Γαλλία και η Γερμανία συμφώνησαν για το σχέδιο αυτό.", {"FR", "DE"}),
        ("Ο πρόεδρος της Γαλλίας μίλησε για την ενέργεια της Ευρώπης.", {"FR"}),
        ("Ο υπουργός ταξίδεψε στην Κύπρο και μετά στον Λίβανο για επαφές.", {"CY", "LB"}),
        ("Οι εκλογές της Κύπρου και του Λιβάνου κρίνουν πολλά φέτος εκεί.", {"CY", "LB"}),
        # Capitals without accents, as in headlines.
        ("ΣΥΝΑΝΤΗΣΗ ΣΤΗΝ ΤΟΥΡΚΙΑ ΓΙΑ ΤΗΝ ΕΛΛΑΔΑ ΚΑΙ ΤΙΣ ΗΠΑ ΣΗΜΕΡΑ.", {"TR", "GR", "US"}),
        # Several words, declined together.
        ("Οι Ηνωμένες Πολιτείες και το Ηνωμένο Βασίλειο αντέδρασαν άμεσα.", {"US", "GB"}),
        ("Η θέση των Ηνωμένων Πολιτειών και του Ηνωμένου Βασιλείου άλλαξε.", {"US", "GB"}),
        ("Επίσκεψη στη Νότια Κορέα και στη Σαουδική Αραβία την επόμενη εβδομάδα.", {"KR", "SA"}),
        # The longer name wins: South Sudan is not also Sudan.
        ("Νέα σύγκρουση στο Νότιο Σουδάν ανησυχεί τους διεθνείς παρατηρητές.", {"SS"}),
        # Indeclinable names.
        ("Το Ιράν απάντησε στο Ισραήλ με ανακοίνωση για το Ιράκ.", {"IR", "IL", "IQ"}),
    ],
)
def test_greek_country_names_are_found_in_every_case(text: str, codes: set[str]) -> None:
    result = detect_countries(greek_context(text, title_end=len(text)))

    assert result.outcome == "success"
    assert {item.country_code for item in result.mentioned} == codes


def test_greek_country_occurrences_point_at_the_article_spelling() -> None:
    text = "Κρίση στην ΤΟΥΡΚΙΑ.\n\nΗ οικονομία της Τουρκίας επιβαρύνθηκε σημαντικά φέτος."
    result = detect_countries(greek_context(text, title_end=19))

    (turkey,) = result.mentioned
    assert [text[item.input_start : item.input_end] for item in turkey.occurrences] == [
        "ΤΟΥΡΚΙΑ",
        "Τουρκίας",
    ]
    # Named in the title and repeated in the body: the story country, as in English.
    assert result.primary is not None and result.primary.country_code == "TR"
    assert result.primary.inferred is True


def test_ambiguous_greek_country_names_are_excluded() -> None:
    # "Γεωργία" is also a woman's name; "Κονγκό" alone could be either Congo.
    text = "Η Γεωργία μίλησε για το ταξίδι της στο Κονγκό και την επιστροφή της."

    result = detect_countries(greek_context(text, title_end=len(text)))

    assert result.outcome == "success"
    assert result.mentioned == ()


def test_greek_country_algorithm_version_is_its_own() -> None:
    greek = detect_countries(greek_context("Η Ελλάδα και η Ιταλία.", title_end=22))
    english = detect_countries(context("France and Italy agreed on the plan today."))

    assert greek.algorithm_version != english.algorithm_version
    assert len(greek.algorithm_version) <= 128


def test_greek_country_lexicon_is_checked_in_with_attribution() -> None:
    path = Path(__file__).parents[1] / "app" / "nlp" / "data" / "countries-el.json"
    payload = json.loads(path.read_text())

    assert "CLDR" in payload["attribution"]
    codes = {item["code"] for item in payload["countries"]}
    english = json.loads((path.parent / "countries-en.json").read_text())
    # The same countries as the English lexicon, each with at least one name.
    assert codes == {item["code"] for item in english["countries"]}
    assert all(item["names"] for item in payload["countries"])


def test_keyword_lookup_matches_greek_keywords_as_typed() -> None:
    from app.nlp.processors import keyword_lookup_key

    # Greek keywords are stored without accents; a query typed with them still finds them.
    assert keyword_lookup_key("Μητσοτάκης") == "μητσοτακησ"
    assert keyword_lookup_key("ΕΝΕΡΓΕΙΑΚΟ σχέδιο") == "ενεργειακο σχεδιο"
    assert keyword_lookup_key("  Energy  Policy ") == "energy policy"
