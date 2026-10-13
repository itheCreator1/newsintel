"""What this install can run: the processors' libraries and models, read from the environment.

The NLP status page and the operations health probe both report it.
"""

import importlib.metadata
import importlib.util

from app.core.config import Settings
from app.nlp.schemas import CapabilityResponse


def capabilities(settings: Settings, greek_ner: bool = False) -> list[CapabilityResponse]:
    """Which processors this install can run, and why not where it cannot."""
    ner_installed = importlib.util.find_spec("spacy") is not None
    models = [settings.nlp_ner_model]
    if greek_ner and settings.nlp_ner_model_el:
        models.append(settings.nlp_ner_model_el)
    missing = [
        model for model in models if not ner_installed or importlib.util.find_spec(model) is None
    ]
    if not settings.nlp_ner_enabled:
        ner_state, detail = "disabled", "Enable the optional local spaCy image to run NER"
    elif not ner_installed:
        ner_state, detail = "configuration_failure", "spaCy is enabled but not installed"
    elif missing:
        ner_state, detail = (
            "configuration_failure",
            f"spaCy model {missing[0]!r} is not installed",
        )
    else:
        ner_state, detail = "available", ", ".join(models)
    return [
        CapabilityResponse(
            name="language",
            state="available",
            version=importlib.metadata.version("lingua-language-detector"),
        ),
        CapabilityResponse(
            name="keywords",
            state="available",
            version=importlib.metadata.version("yake"),
            detail="English and Greek",
        ),
        CapabilityResponse(
            name="countries",
            state="available",
            version="iso-3166-reviewed-1",
            detail="English and Greek",
        ),
        CapabilityResponse(
            name="entities",
            state=ner_state,
            version=importlib.metadata.version("spacy") if ner_installed else None,
            detail=detail,
        ),
    ]
