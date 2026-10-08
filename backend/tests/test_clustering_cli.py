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


def test_reprocess_nlp_passes_the_language_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unlike the cases above this one gets past argument checks, so the reprocessing and
    # asyncio.run are both replaced: a real asyncio.run would unset the event loop that the
    # session-scoped async tests share.
    calls: list[dict[str, object]] = []

    def fake_reprocessing(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr("app.cli.run_nlp_reprocessing", fake_reprocessing)
    monkeypatch.setattr("app.cli.asyncio.run", lambda _awaitable: None)
    _run(monkeypatch, "reprocess-nlp", "--processors", "entities", "--all", "--language", "el")

    assert calls == [
        {
            "run_id": None,
            "processors": ("entities",),
            "selection": {"all": True, "language": "el"},
            "apply": False,
        }
    ]


def test_language_is_rejected_outside_reprocess_nlp(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as failure:
        _run(monkeypatch, "recluster", "--all", "--language", "el")
    assert failure.value.code == 2
    assert "--language applies to reprocess-nlp only" in capsys.readouterr().err
