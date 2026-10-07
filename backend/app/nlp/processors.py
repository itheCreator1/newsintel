import functools
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

# spaCy labels for dates, times and amounts ("Last week", "40%", "two") are never named things.
DROPPED_ENTITY_LABELS = frozenset(
    {"DATE", "TIME", "PERCENT", "MONEY", "QUANTITY", "ORDINAL", "CARDINAL"}
)

_TIME_WORDS = (
    r"(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|week|month|year|"
    r"decade|century|day|night|morning|afternoon|evening|quarter|season|spring|summer|autumn|"
    r"fall|winter)s?"
)
# Time phrases the model sometimes mislabels as ORG/PERSON/EVENT, e.g. "Last week" or "Tuesday".
_TIME_PHRASE = re.compile(
    r"(?:today|tonight|yesterday|tomorrow|now|recently|"
    r"(?:(?:the|this|last|next|past|previous|coming|early|late|earlier|later|every|each)\s+)*"
    r"(?:(?:\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|few|several|many)\s+)?"
    + _TIME_WORDS
    + r"(?:\s+(?:ago|earlier|later))?)"
)
_LEADING_JUNK = re.compile(r"^[\W_]+|[\W_]+$")


def _is_junk_entity(text: str, label: str) -> bool:
    if label in DROPPED_ENTITY_LABELS:
        return True
    cleaned = _LEADING_JUNK.sub("", " ".join(text.casefold().split()))
    # A fragment ("'s", "U") or a bare number; "B-1" and "F1" have one letter but are names.
    if not any(character.isalpha() for character in cleaned) or (
        sum(character.isalnum() for character in cleaned) < 2
    ):
        return True
    return _TIME_PHRASE.fullmatch(cleaned) is not None


# Names NER gives a country beyond the matcher's lexicon. "US" or "UK" can't be matched in free
# text ("us" is a pronoun), but as a place name spaCy found they are unambiguous.
_ENTITY_COUNTRY_ALIASES = {
    "us": "US",
    "usa": "US",
    "america": "US",
    "uk": "GB",
    "uae": "AE",
    "prc": "CN",
    "drc": "CD",
}
# How news names a country where the lexicon's first ISO short name reads oddly.
_COUNTRY_DISPLAY = {
    "BN": "Brunei",
    "CD": "DR Congo",
    "GB": "United Kingdom",
    "KP": "North Korea",
    "KR": "South Korea",
    "LA": "Laos",
    "SY": "Syria",
    "US": "United States",
    "VA": "Vatican City",
    "VG": "British Virgin Islands",
    "VI": "US Virgin Islands",
    "VN": "Vietnam",
}
_PLACE_LABELS = frozenset({"GPE", "LOC"})
_LEADING_THE = re.compile(r"^the\s+", re.IGNORECASE)
_POSSESSIVE = re.compile(r"[\'’]s?$")
_DOTTED_ACRONYM = re.compile(r"(?:[A-Za-z]\.){2,}")
_UNFINISHED_ACRONYM = re.compile(r"(?:[a-z]\.)+[a-z]")


@functools.cache
def _entity_countries() -> dict[str, tuple[str, str]]:
    """Every country name or alias, normalized, to the country's (code, display name)."""
    _, lexicon = _country_lexicon()
    display = {
        code: _COUNTRY_DISPLAY.get(code, names[0].split(",")[0]) for code, names in lexicon.items()
    }
    names = {
        _normalized_name(name): code
        for code, values in lexicon.items()
        for name in values
        if name.casefold() not in _AMBIGUOUS_COUNTRY_NAMES
    }
    names.update(_ENTITY_COUNTRY_ALIASES)
    return {name: (code, display[code]) for name, code in names.items()}


def _normalized_name(text: str) -> str:
    """One key per name: "The U.S.", "U.S.'s" and "US" all become "us"."""
    text = _POSSESSIVE.sub("", " ".join(text.split())) or text
    text = _LEADING_THE.sub("", text) or text
    if _DOTTED_ACRONYM.fullmatch(text):
        text = text.replace(".", "")
    return text.casefold()


def canonical_entity(text: str, label: str, mapped: str) -> tuple[str, str, str]:
    """The (entity type, normalized text, display text) that identify an extracted name.

    The display keeps the article's own spelling ("The Hague"); only the identity is folded.
    """
    display = _POSSESSIVE.sub("", " ".join(text.split())) or text
    normalized = _normalized_name(text)
    if label in _PLACE_LABELS:
        country = _entity_countries().get(normalized)
        if country is not None:
            return "GPE", country[1].casefold(), country[1]
    return mapped, normalized, display


def country_entity_names(prefix: str) -> frozenset[str]:
    """Normalized texts of the country entities one of whose names starts with `prefix`.

    Lets a lookup for "US" or "U.S." reach the entity those spellings were folded into, and keeps
    it there while the acronym is still being typed ("U.S").
    """
    key = _normalized_name(prefix)
    if _UNFINISHED_ACRONYM.fullmatch(key):
        key = key.replace(".", "")
    return frozenset(
        display.casefold()
        for name, (_, display) in _entity_countries().items()
        if key and name.startswith(key)
    )


def _merge_short_person_names(
    grouped: dict[tuple[str, str, str], list[Occurrence]],
    display: dict[tuple[str, str, str], str],
) -> None:
    """Fold "Trump" into "Donald Trump" when the article names only one Trump in full.

    A surname alone is merged only when every longer name ending in it shares a first name, so
    "Trump" beside both "Donald Trump" and "Melania Trump" stays as it is.
    """
    people = sorted(
        (key for key in grouped if key[0] == "PERSON"), key=lambda key: -len(key[1].split())
    )
    for key in people:
        tokens = key[1].split()
        longer = [
            other
            for other in people
            if other in grouped
            and len(other[1].split()) > len(tokens)
            and other[1].split()[-len(tokens) :] == tokens
        ]
        if not longer or len({other[1].split()[0] for other in longer}) != 1:
            continue
        target = max(longer, key=lambda other: (len(grouped[other]), other[1]))
        grouped[target] = sorted(
            [*grouped[target], *grouped.pop(key)], key=lambda item: item.input_start
        )
        del display[key]


# Loading a model takes far longer than annotating one article, so each model is loaded once per
# process. The excluded components never feed the NER component, so the entities are identical to
# the full pipeline's. The parser stays: NER never lets an entity cross a sentence boundary it set.
_NER_EXCLUDED_COMPONENTS = ["tagger", "attribute_ruler", "lemmatizer"]
_ner_pipelines: dict[str, Any] = {}


def _ner_pipeline(model: str) -> Any:
    pipeline = _ner_pipelines.get(model)
    if pipeline is None:
        spacy = importlib.import_module("spacy")
        try:
            pipeline = spacy.load(model, exclude=_NER_EXCLUDED_COMPONENTS)
        except OSError as exc:
            raise ConfigurationError(f"spaCy model {model!r} is not installed") from exc
        _ner_pipelines[model] = pipeline
    return pipeline


def extract_entities(context: ProcessorContext) -> EntityResult:
    algorithm = "spacy-ner-map-3"
    if context.language != "en":
        return EntityResult("unsupported_language", (), algorithm, None)
    if not context.ner_enabled:
        return EntityResult("disabled", (), algorithm, None)
    if importlib.util.find_spec("spacy") is None:
        raise ConfigurationError("spaCy is not installed but NLP NER is enabled")
    pipeline = _ner_pipeline(context.ner_model)
    document = pipeline(context.text)
    grouped: dict[tuple[str, str, str], list[Occurrence]] = {}
    display: dict[tuple[str, str, str], str] = {}
    for entity in document.ents:
        original_label = str(entity.label_)
        if _is_junk_entity(str(entity.text), original_label):
            continue
        mapped, normalized, text = canonical_entity(
            str(entity.text), original_label, ENTITY_TYPE_MAP.get(original_label, "OTHER")
        )
        key = (mapped, normalized, original_label)
        grouped.setdefault(key, []).append(_occurrence(context, entity.start_char, entity.end_char))
        display.setdefault(key, text)
    _merge_short_person_names(grouped, display)
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
