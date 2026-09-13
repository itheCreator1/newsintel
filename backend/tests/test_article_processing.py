from pathlib import Path

import pytest

from app.articles.extraction import EmptyExtraction, TrafilaturaExtractor, normalize_text
from app.articles.processing import ArticleHttpStatus, _failure, delete_after_commit
from app.articles.storage import LocalObjectStorage, ObjectStorageError
from app.feeds.scheduling import next_retry_delay


def test_normalized_content_ignores_insignificant_whitespace() -> None:
    assert normalize_text("  One\r\n\r\n  two  ") == "One\n\ntwo"


def test_extractor_rejects_a_page_without_readable_text() -> None:
    with pytest.raises(EmptyExtraction):
        TrafilaturaExtractor().extract(b"<html><body></body></html>")


def test_local_storage_uses_opaque_keys_and_atomic_round_trip(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)
    key = storage.put(b"<html>story</html>")
    assert "/" not in key and "." not in key
    assert storage.get(key) == b"<html>story</html>"
    storage.delete(key)
    assert storage.get(key) is None


def test_retry_after_is_bounded() -> None:
    assert next_retry_delay(1, retry_after=9999) == 300


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (429, ("http_transient", True)),
        (503, ("http_transient", True)),
        (404, ("http_permanent", False)),
    ],
)
def test_article_http_failures_are_classified_for_retry(
    status_code: int, expected: tuple[str, bool]
) -> None:
    assert _failure(ArticleHttpStatus(status_code)) == expected


def test_committed_extraction_survives_object_cleanup_failure(tmp_path: Path) -> None:
    storage = LocalObjectStorage(tmp_path)

    def failed_delete(_key: str) -> None:
        raise ObjectStorageError("disk unavailable")

    storage.delete = failed_delete  # type: ignore[method-assign]

    assert delete_after_commit(storage, "stale-object", article_id="article-1") is False
