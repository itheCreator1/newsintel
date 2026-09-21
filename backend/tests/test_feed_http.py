import httpx
import pytest

from app.core.config import Settings
from app.feeds import http
from app.feeds.network import ValidatedDestination


@pytest.mark.parametrize(
    "fetch",
    [
        lambda url, settings: http.fetch_feed_http(url, {}, settings),
        http.fetch_page_http,
    ],
)
@pytest.mark.asyncio
async def test_relative_redirect_keeps_the_hostname(
    monkeypatch: pytest.MonkeyPatch, fetch: object
) -> None:
    # httpx's URL.netloc is bytes, so str() never matched and a relative redirect kept the pinned
    # IP as its host: the next hop sent SNI and verified the certificate against the IP.
    validated: list[str] = []

    async def validate(url: str, _allowed: object = None) -> ValidatedDestination:
        validated.append(url)
        return ValidatedDestination(url, httpx.URL(url).host, ("93.184.216.34",))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new?x=1"})
        return httpx.Response(200, content=b"ok")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(http, "validate_public_url", validate)
    monkeypatch.setattr(
        http.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    response = await fetch("https://news.example/old", Settings())  # type: ignore[operator]
    assert validated == ["https://news.example/old", "https://news.example/new?x=1"]
    assert response.url == "https://news.example/new?x=1"
