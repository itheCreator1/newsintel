import os

import pytest

from app.nlp.input import InputSection
from app.nlp.processors import ProcessorContext, extract_entities

pytestmark = pytest.mark.skipif(
    os.getenv("NEWSINTEL_RUN_NER_TESTS") != "1",
    reason="requires the optional pinned spaCy model",
)


def test_pinned_spacy_model_extracts_real_english_entities() -> None:
    text = "Barack Obama met Microsoft executives in London on Monday."
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
