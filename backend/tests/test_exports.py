import csv
import io
import json
import uuid
from datetime import UTC, date, datetime

from app.exports.service import ExportRow, export_response, render

ROW = ExportRow(
    article_id=uuid.UUID("11111111-1111-4111-8111-111111111111"),
    title='Fire, smoke and a "state of emergency"',
    url="https://wire.example/fire",
    date=datetime(2026, 3, 2, 8, 30, tzinfo=UTC),
    sources=["Wire", "Daily"],
    entities=["Athens", "Ministry of Health"],
)


def test_csv_has_one_row_per_article_with_lists_joined() -> None:
    table = list(csv.reader(io.StringIO(render([ROW], "csv").decode("utf-8-sig"))))
    assert table == [
        ["article_id", "title", "url", "date", "sources", "entities"],
        [
            str(ROW.article_id),
            'Fire, smoke and a "state of emergency"',
            "https://wire.example/fire",
            "2026-03-02T08:30:00+00:00",
            "Wire; Daily",
            "Athens; Ministry of Health",
        ],
    ]


def test_csv_cells_that_a_spreadsheet_would_run_as_formulas_are_kept_as_text() -> None:
    risky = ExportRow(ROW.article_id, '=HYPERLINK("x")', "+1", ROW.date, ["@Wire"], ["-Athens"])
    (_, row) = list(csv.reader(io.StringIO(render([risky], "csv").decode("utf-8-sig"))))
    assert row[1:3] == ['\'=HYPERLINK("x")', "'+1"]
    assert row[4:] == ["'@Wire", "'-Athens"]


def test_json_keeps_lists_as_lists() -> None:
    assert json.loads(render([ROW], "json")) == [
        {
            "article_id": str(ROW.article_id),
            "title": ROW.title,
            "url": ROW.url,
            "date": "2026-03-02T08:30:00+00:00",
            "sources": ["Wire", "Daily"],
            "entities": ["Athens", "Ministry of Health"],
        }
    ]


def test_the_response_is_a_named_download() -> None:
    response = export_response([ROW], "csv", "event", today=date(2026, 3, 4))
    assert response.media_type == "text/csv"
    assert response.headers["content-disposition"] == (
        'attachment; filename="newsintel-event-2026-03-04.csv"'
    )
    assert export_response([], "json", "search", today=date(2026, 3, 4)).media_type == (
        "application/json"
    )
