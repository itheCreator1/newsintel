import os
from pathlib import Path

from app.nlp.input import InputSection
from app.nlp.processors import ProcessorContext, extract_entities


def test_pinned_spacy_model_extracts_real_english_entities() -> None:
    if os.getenv("NEWSINTEL_RUN_NER_TESTS") != "1":
        requirements = Path(__file__).parents[1] / "requirements-ner.txt"
        assert "spacy==3.8.16" in requirements.read_text()
        assert "en_core_web_sm-3.8.0" in requirements.read_text()
        return
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
