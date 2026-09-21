import re
import sys
from datetime import UTC, date, datetime, timedelta
from email.utils import format_datetime, parsedate_to_datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
RECOVERY_FAILURES = 3
# The day after the newest fixture pubDate; frontend/e2e/fixture-dates.ts mirrors it.
ANCHOR = date(2026, 9, 14)
PUB_DATE = re.compile(r"<pubDate>(.*?)</pubDate>")


def shift_pub_dates(xml: str, today: date) -> str:
    """Move every pubDate forward by whole days so the newest lands on `today - 1`."""
    offset = timedelta(days=max(0, (today - ANCHOR).days))

    def shift(match: re.Match[str]) -> str:
        moved = parsedate_to_datetime(match.group(1)) + offset
        return f"<pubDate>{format_datetime(moved, usegmt=True)}</pubDate>"

    return PUB_DATE.sub(shift, xml)


class FixtureHandler(SimpleHTTPRequestHandler):
    recovery_requests = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self) -> None:
        if self.path == "/permanent.html":
            self.send_error(404, "permanent fixture failure")
            return
        if self.path == "/transient.html":
            self.send_error(503, "transient fixture failure")
            return
        if self.path == "/recover.html":
            type(self).recovery_requests += 1
            if type(self).recovery_requests <= RECOVERY_FAILURES:
                self.send_error(503, "recoverable fixture failure")
                return
            self.path = "/article-changed.html"
        super().do_GET()

    def copyfile(self, source: Any, outputfile: Any) -> None:
        # Shifted GMT dates keep their length, so Content-Length and 304 handling still hold.
        if not self.path.endswith(".xml"):
            super().copyfile(source, outputfile)
            return
        # ponytail: offset computed per request, freeze a test clock if midnight-crossing runs flake
        outputfile.write(shift_pub_dates(source.read().decode(), datetime.now(UTC).date()).encode())


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    ThreadingHTTPServer(("0.0.0.0", port), FixtureHandler).serve_forever()
