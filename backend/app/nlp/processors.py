import importlib
import importlib.metadata
import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import yake  # type: ignore[import-untyped]
from lingua import LanguageDetectorBuilder

from app.nlp.input import InputSection

Outcome = Literal["success", "unsupported_language", "disabled"]


class ConfigurationError(RuntimeError):
    pass


class InputTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class ProcessorMetadata:
    name: str
    version: str
    algorithm_version: str
    supported_languages: frozenset[str]
    dependencies: tuple[str, ...]


class Processor[T](Protocol):
    metadata: ProcessorMetadata

    def process(self, context: "ProcessorContext") -> T: ...


@dataclass(frozen=True)
class ProcessorContext:
    text: str
    input_fingerprint: str
    sections: tuple[InputSection, ...]
    language: str | None
    stop_words: frozenset[str]
    ner_enabled: bool
    ner_model: str


@dataclass(frozen=True)
class Occurrence:
    section: str
    reference_id: str | None
    start: int
    end: int
    input_start: int
    input_end: int


@dataclass(frozen=True)
class LanguageResult:
    outcome: Outcome
    language: str
    confidence: float
    margin: float
    algorithm_version: str


@dataclass(frozen=True)
class KeywordValue:
    text: str
    normalized_text: str
    kind: str
    occurrence_count: int
    raw_score: float
    relevance: float
    occurrences: tuple[Occurrence, ...]


@dataclass(frozen=True)
class KeywordResult:
    outcome: Outcome
    keywords: tuple[KeywordValue, ...]
    algorithm_version: str


@dataclass(frozen=True)
class EntityValue:
    text: str
    normalized_text: str
    entity_type: str
    original_label: str
    occurrence_count: int
    relevance: float
    occurrences: tuple[Occurrence, ...]


@dataclass(frozen=True)
class EntityResult:
    outcome: Outcome
    entities: tuple[EntityValue, ...]
    algorithm_version: str
    model_version: str | None


@dataclass(frozen=True)
class CountryValue:
    country_code: str
    occurrence_count: int
    occurrences: tuple[Occurrence, ...]
    inferred: bool = False


@dataclass(frozen=True)
class CountryResult:
    outcome: Outcome
    mentioned: tuple[CountryValue, ...]
    primary: CountryValue | None
    algorithm_version: str


_language_detector: Any = None


def validate_input_size(context: ProcessorContext, maximum: int) -> None:
    if len(context.text) > maximum:
        raise InputTooLarge(f"NLP input has {len(context.text)} characters; maximum is {maximum}")


def _occurrence(context: ProcessorContext, start: int, end: int) -> Occurrence:
    for section in context.sections:
        if section.start <= start and end <= section.end:
            return Occurrence(
                section.kind,
                section.reference_id,
                start - section.start,
                end - section.start,
                start,
                end,
            )
    return Occurrence("input", None, start, end, start, end)


def _matches(context: ProcessorContext, value: str) -> tuple[Occurrence, ...]:
    pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)", re.IGNORECASE)
    return tuple(
        _occurrence(context, match.start(), match.end()) for match in pattern.finditer(context.text)
    )


def detect_language(context: ProcessorContext) -> LanguageResult:
    alpha_count = sum(character.isalpha() for character in context.text)
    version = importlib.metadata.version("lingua-language-detector")
    algorithm = f"lingua-{version}-thresholds-1"
    if alpha_count < 50:
        return LanguageResult("success", "und", 0.0, 0.0, algorithm)
    global _language_detector
    if _language_detector is None:
        _language_detector = LanguageDetectorBuilder.from_all_languages().build()
    values = _language_detector.compute_language_confidence_values(context.text)
    if not values:
        return LanguageResult("success", "und", 0.0, 0.0, algorithm)
    top = values[0]
    second = values[1].value if len(values) > 1 else 0.0
    confidence = float(top.value)
    margin = confidence - float(second)
    code = top.language.iso_code_639_1.name.lower()
    if confidence < 0.80 or margin < 0.20:
        code = "und"
    return LanguageResult("success", code, confidence, margin, algorithm)


def extract_keywords(context: ProcessorContext) -> KeywordResult:
    version = importlib.metadata.version("yake")
    algorithm = f"yake-{version}-newsintel-relevance-1"
    if context.language != "en":
        return KeywordResult("unsupported_language", (), algorithm)
    extractor = yake.KeywordExtractor(
        lan="en", n=3, top=50, dedup_lim=0.9, stopwords=set(context.stop_words)
    )
    values: dict[str, KeywordValue] = {}
    for phrase, score_value in extractor.extract_keywords(context.text):
        normalized = " ".join(phrase.casefold().split())
        occurrences = _matches(context, phrase)
        if not normalized or not occurrences or not 1 <= len(normalized.split()) <= 3:
            continue
        score = float(score_value)
        relevance = 1.0 / (1.0 + max(score, 0.0))
        candidate = KeywordValue(
            phrase,
            normalized,
            "keyword" if len(normalized.split()) == 1 else "keyphrase",
            len(occurrences),
            score,
            relevance,
            occurrences,
        )
        previous = values.get(normalized)
        if previous is None or candidate.raw_score < previous.raw_score:
            values[normalized] = candidate
    ranked = sorted(values.values(), key=lambda item: (-item.relevance, item.normalized_text))[:50]
    return KeywordResult("success", tuple(ranked), algorithm)


ENTITY_TYPE_MAP = {
    "PERSON": "PERSON",
    "ORG": "ORG",
    "GPE": "GPE",
    "LOC": "LOCATION",
    "FAC": "LOCATION",
    "EVENT": "EVENT",
    "PRODUCT": "PRODUCT",
}


def extract_entities(context: ProcessorContext) -> EntityResult:
    algorithm = "spacy-ner-map-1"
    if context.language != "en":
        return EntityResult("unsupported_language", (), algorithm, None)
    if not context.ner_enabled:
        return EntityResult("disabled", (), algorithm, None)
    if importlib.util.find_spec("spacy") is None:
        raise ConfigurationError("spaCy is not installed but NLP NER is enabled")
    spacy = importlib.import_module("spacy")
    try:
        pipeline = spacy.load(context.ner_model)
    except OSError as exc:
        raise ConfigurationError(f"spaCy model {context.ner_model!r} is not installed") from exc
    document = pipeline(context.text)
    grouped: dict[tuple[str, str, str], list[Occurrence]] = {}
    display: dict[tuple[str, str, str], str] = {}
    for entity in document.ents:
        original_label = str(entity.label_)
        mapped = ENTITY_TYPE_MAP.get(original_label, "OTHER")
        text = str(entity.text)
        normalized = " ".join(text.casefold().split())
        key = (mapped, normalized, original_label)
        grouped.setdefault(key, []).append(_occurrence(context, entity.start_char, entity.end_char))
        display.setdefault(key, text)
    values = [
        EntityValue(
            display[key],
            key[1],
            key[0],
            key[2],
            len(occurrences),
            min(1.0, len(occurrences) / 5),
            tuple(occurrences),
        )
        for key, occurrences in grouped.items()
    ]
    values.sort(key=lambda item: (-item.relevance, item.normalized_text, item.entity_type))
    model_version = str(pipeline.meta.get("version", "unknown"))
    return EntityResult("success", tuple(values), algorithm, model_version)


_LEXICON_PATH = Path(__file__).with_name("data") / "countries-en.json"
_AMBIGUOUS_COUNTRY_NAMES = frozenset({"congo", "georgia"})


def _country_lexicon() -> tuple[str, dict[str, tuple[str, ...]]]:
    payload = json.loads(_LEXICON_PATH.read_text())
    countries = {item["code"]: tuple(item["names"]) for item in payload["countries"]}
    return str(payload["version"]), countries


def detect_countries(context: ProcessorContext) -> CountryResult:
    version, lexicon = _country_lexicon()
    algorithm = f"country-explicit-{version}-primary-1"
    if context.language != "en":
        return CountryResult("unsupported_language", (), None, algorithm)
    by_country: dict[str, list[Occurrence]] = {}
    for code, names in lexicon.items():
        for name in names:
            if name.casefold() in _AMBIGUOUS_COUNTRY_NAMES:
                continue
            by_country.setdefault(code, []).extend(_matches(context, name))
    mentioned = tuple(
        CountryValue(
            code, len(occurrences), tuple(sorted(occurrences, key=lambda item: item.input_start))
        )
        for code, occurrences in sorted(by_country.items())
        if occurrences
    )
    title_codes = {
        item.country_code
        for item in mentioned
        if any(occurrence.section == "title" for occurrence in item.occurrences)
    }
    primary = None
    if len(title_codes) == 1:
        code = next(iter(title_codes))
        item = next(value for value in mentioned if value.country_code == code)
        if any(occurrence.section != "title" for occurrence in item.occurrences):
            primary = CountryValue(code, item.occurrence_count, item.occurrences, inferred=True)
    return CountryResult("success", mentioned, primary, algorithm)
