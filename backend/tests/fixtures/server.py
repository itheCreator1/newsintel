from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
RECOVERY_FAILURES = 3


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


ThreadingHTTPServer(("0.0.0.0", 80), FixtureHandler).serve_forever()
