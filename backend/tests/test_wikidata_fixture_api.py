"""The fixture server's stand-in for api.php answers the real client as Wikidata would.

The e2e stack points the worker at it, so the browser test of linking an entity never reaches
Wikidata; this checks, over a real socket, that the client reads what the stand-in serves.
"""

import importlib.util
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from wikidata_fake import Clock, settings

from app.wikidata.client import WikidataClient
from app.wikidata.throttle import MemoryThrottle

FIXTURES = Path(__file__).parent / "fixtures"
_spec = importlib.util.spec_from_file_location("fixture_server", FIXTURES / "server.py")
assert _spec is not None and _spec.loader is not None
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def api_url() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.FixtureHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/w/api.php"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _client(url: str) -> WikidataClient:
    config = settings(wikidata_url=url)
    clock = Clock()
    # A transport of its own: no proxy from the environment between the client and the socket.
    return WikidataClient(
        config,
        MemoryThrottle(config, clock=clock, sleep=clock.sleep),
        transport=httpx.AsyncHTTPTransport(),
    )


async def test_search_light_items_and_claims(api_url: str) -> None:
    async with _client(api_url) as client:
        assert await client.search("Ada Lindqvist", "en") == ["Q900001", "Q900002"]
        assert await client.search("Nobody", "en") == []
        diplomat, film = await client.items(["Q900001", "Q900002"])
        assert diplomat.labels == {"en": "Ada Lindqvist", "el": "Άντα Λίντκβιστ"}
        assert diplomat.descriptions["en"] == "Swedish diplomat (invented for the tests)"
        assert not diplomat.claims_fetched and diplomat.sitelinks == 12
        assert film.labels == {"en": "Ada Lindqvist"}
        assert await client.claim_items("Q900001", "P31") == ["Q5"]
        assert await client.claim_items("Q900002", "P31") == ["Q11424"]


async def test_info_then_full_items_read_the_identifiers(api_url: str) -> None:
    async with _client(api_url) as client:
        pages = await client.info(["Q900001", "Q404"])
        assert pages["Q900001"].state == "ok" and pages["Q900001"].revision == 5001
        assert pages["Q404"].state == "missing"
        (item,) = await client.full_items(["Q900001"], pages)
    assert item.claims_fetched
    assert item.instance_of == ["Q5"]
    assert item.aliases == {"en": ["A. Lindqvist"]}
    assert item.ids == {"viaf": "900001", "isni": "0000 0000 9000 0001"}


async def test_the_feeds_are_still_served(api_url: str) -> None:
    async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport()) as http:
        response = await http.get(api_url.replace("/w/api.php", "/feed.xml"))
    assert response.status_code == 200
