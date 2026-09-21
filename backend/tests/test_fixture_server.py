import importlib.util
from datetime import date, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
_spec = importlib.util.spec_from_file_location("fixture_server", FIXTURES / "server.py")
assert _spec is not None and _spec.loader is not None
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def _dates(xml: str) -> list[date]:
    return [parsedate_to_datetime(raw).date() for raw in server.PUB_DATE.findall(xml)]


def test_fixture_feeds_shift_so_the_newest_date_is_yesterday() -> None:
    newest = max(d for feed in FIXTURES.glob("*.xml") for d in _dates(feed.read_text()))
    assert newest == server.ANCHOR - timedelta(days=1)
    for today in (date(2026, 12, 25), date(2027, 10, 1)):
        for feed in FIXTURES.glob("*.xml"):
            xml = feed.read_text()
            shifted = server.shift_pub_dates(xml, today)
            # Same byte length keeps the static server's Content-Length valid.
            assert len(shifted.encode()) == len(xml.encode())
            offset = today - server.ANCHOR
            assert _dates(shifted) == [d + offset for d in _dates(xml)]


def test_fixture_feeds_are_not_shifted_backwards() -> None:
    xml = (FIXTURES / "feed.xml").read_text()
    assert server.shift_pub_dates(xml, date(2026, 1, 1)) == xml
