from pathlib import Path

import pytest

from app.nlp.input import InputSection
from app.nlp.processors import (
    ConfigurationError,
    InputTooLarge,
    ProcessorContext,
    detect_countries,
    detect_language,
    extract_entities,
    extract_keywords,
    validate_input_size,
)

ENGLISH_TEXT = (
    "European officials discussed a new energy policy in Brussels after markets reacted. "
    "The policy includes renewable energy investment and stronger regional energy security."
)


def context(text: str, *, language: str = "en", ner_enabled: bool = False) -> ProcessorContext:
    return ProcessorContext(
        text=text,
        input_fingerprint="a" * 64,
        sections=(InputSection("title", None, 0, text.find(".") + 1),),
        language=language,
        stop_words=frozenset({"after", "and", "the"}),
        ner_enabled=ner_enabled,
        ner_model="en_core_web_sm",
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


def test_ambiguous_country_aliases_are_excluded() -> None:
    result = detect_countries(context("Georgia tells us a story about policy and elections. " * 3))

    assert result.mentioned == ()
    assert result.primary is None


def test_country_lexicon_is_checked_in_with_attribution() -> None:
    lexicon = Path(__file__).parents[1] / "app" / "nlp" / "data" / "countries-en.json"

    assert lexicon.exists()
    assert "ISO 3166-1" in lexicon.read_text()
    assert '"code": "ZW"' in lexicon.read_text()
