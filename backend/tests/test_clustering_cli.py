import pytest

from app.cli import main


def _run(monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    """Every case here must fail before `main()` reaches the database or an event loop."""
    monkeypatch.setattr("sys.argv", ["app.cli", *argv])
    main()


def test_recluster_rejects_a_naive_date_with_a_clean_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, "recluster", "--from-date", "2026-01-01", "--apply")
    assert failure.value.code == 2
    assert "must include a UTC offset" in capsys.readouterr().err


def test_recluster_requires_exactly_one_selection_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, "recluster")
    assert failure.value.code == 2
    assert "exactly one of --article-id" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        _run(monkeypatch, "recluster", "--all", "--from-date", "2026-01-01T00:00:00Z")


def test_recluster_rejects_a_batch_size_outside_the_supported_range(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, "recluster", "--all", "--apply", "--batch-size", "0")
    assert failure.value.code == 2
    assert "batch size" in capsys.readouterr().err


def test_recluster_rejects_a_non_uuid_article_id(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, "recluster", "--article-id", "not-a-uuid")
    assert failure.value.code == 2
    assert "must be UUIDs" in capsys.readouterr().err
