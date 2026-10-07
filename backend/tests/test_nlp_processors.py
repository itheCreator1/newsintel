import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.nlp.input import InputSection
from app.nlp.processors import (
    ConfigurationError,
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
    monkeypatch.setattr("app.nlp.processors.importlib.import_module", lambda _: fake_spacy)
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
    monkeypatch.setattr("app.nlp.processors.importlib.import_module", lambda _: fake_spacy)
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


def test_extract_entities_merges_spellings_of_one_country(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "The U.S. and the United States, or simply US, are one country."
    spans = [("U.S.", "GPE"), ("United States", "GPE"), ("US", "GPE")]

    entities = _extract_with_spans(monkeypatch, text, spans)

    assert entities == {("GPE", "united states", "United States", 3)}


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
    monkeypatch.setattr(
        "app.nlp.processors.importlib.import_module", lambda _: SimpleNamespace(load=load)
    )
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
    assert result.algorithm_version == "spacy-ner-el-1"


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


def test_greek_model_changes_the_entity_fingerprint_only_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings
    from app.nlp import service

    def fingerprint(settings: Settings) -> dict[str, str]:
        monkeypatch.setattr(service, "get_settings", lambda: settings)
        return {name: service.configuration_fingerprint(name) for name in service.PROCESSORS}

    base = Settings(nlp_ner_enabled=True)
    expected = hashlib.sha256(
        json.dumps(
            {
                "processor": "entities",
                "version": "1",
                "stop_words": None,
                "ner_enabled": True,
                "ner_model": "en_core_web_sm",
                "max_input_characters": base.nlp_max_input_characters,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()
    without = fingerprint(base)
    with_greek = fingerprint(Settings(nlp_ner_enabled=True, nlp_ner_model_el="el_core_news_sm"))

    # An install that leaves Greek off keeps the fingerprints its articles were processed with.
    assert without["entities"] == expected
    assert with_greek["entities"] != expected
    assert {k: v for k, v in with_greek.items() if k != "entities"} == {
        k: v for k, v in without.items() if k != "entities"
    }
