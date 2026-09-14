import hashlib
import uuid
from datetime import UTC, datetime

from app.nlp.input import InputSection, build_input_document
from app.nlp.service import next_retry_at


def test_input_document_is_deterministic_and_deduplicates_rss_descriptions() -> None:
    document = build_input_document(
        title="  A headline  ",
        body=None,
        descriptions=[
            (datetime(2026, 1, 2, tzinfo=UTC), uuid.UUID(int=2), "Same summary"),
            (datetime(2026, 1, 1, tzinfo=UTC), uuid.UUID(int=1), "Same summary"),
            (datetime(2026, 1, 3, tzinfo=UTC), uuid.UUID(int=3), "Second summary"),
        ],
    )

    assert document.text == "A headline\n\nSame summary\n\nSecond summary"
    assert document.sections == (
        InputSection("title", None, 0, 10),
        InputSection("rss_description", str(uuid.UUID(int=1)), 12, 24),
        InputSection("rss_description", str(uuid.UUID(int=3)), 26, 40),
    )
    assert document.fingerprint == hashlib.sha256(document.text.encode()).hexdigest()


def test_extracted_body_replaces_rss_descriptions_in_canonical_input() -> None:
    document = build_input_document(
        title="Headline",
        body="Extracted body",
        descriptions=[(datetime.now(UTC), uuid.uuid4(), "RSS summary")],
    )

    assert document.text == "Headline\n\nExtracted body"
    assert [section.kind for section in document.sections] == ["title", "body"]


def test_nlp_retry_uses_required_capped_exponential_backoff() -> None:
    now = datetime(2026, 9, 14, tzinfo=UTC)

    assert next_retry_at(now, 1) == datetime(2026, 9, 14, 0, 0, 30, tzinfo=UTC)
    assert next_retry_at(now, 2) == datetime(2026, 9, 14, 0, 1, tzinfo=UTC)
    assert next_retry_at(now, 10) == datetime(2026, 9, 14, 0, 15, tzinfo=UTC)
