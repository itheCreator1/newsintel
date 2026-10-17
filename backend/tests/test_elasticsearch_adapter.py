"""A request Elasticsearch refuses is told apart from one it could not serve (fix plan Δ1).

Every HTTP error used to be "unavailable", so a query Elasticsearch rejected read as an outage,
and a bulk body it would never accept was retried for ever.
"""

from collections.abc import AsyncIterator, Callable

import httpx
import pytest
import pytest_asyncio

from app.search import elasticsearch
from app.search.elasticsearch import (
    BulkDocument,
    ElasticsearchAdapter,
    ElasticsearchRejected,
    ElasticsearchUnavailable,
)

URL = "http://elasticsearch.test"
PARSING = {
    "error": {"type": "parsing_exception", "reason": "Unknown key for a START_OBJECT in [qury]."},
    "status": 400,
}

Handler = Callable[[httpx.Request], httpx.Response]


@pytest_asyncio.fixture
async def serve(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Callable[[Handler], None]]:
    clients: list[httpx.AsyncClient] = []

    def install(handler: Handler) -> None:
        client = httpx.AsyncClient(base_url=URL, transport=httpx.MockTransport(handler))
        clients.append(client)
        monkeypatch.setattr(elasticsearch, "_shared_client", client)

    yield install
    for client in clients:
        await client.aclose()


def _status(code: int, body: object = None) -> Handler:
    return lambda _request: httpx.Response(code, json=body or {})


@pytest.mark.asyncio
async def test_a_400_raises_rejected_with_its_status(serve: Callable[[Handler], None]) -> None:
    serve(_status(400, PARSING))
    with pytest.raises(ElasticsearchRejected) as raised:
        await ElasticsearchAdapter(URL).search({"qury": {}})
    assert raised.value.status == 400
    assert not isinstance(raised.value, ElasticsearchUnavailable)
    # The log says what Elasticsearch objected to, not only the status line.
    assert "Unknown key for a START_OBJECT" in str(raised.value)


@pytest.mark.parametrize("code", [408, 429, 500, 503])
@pytest.mark.asyncio
async def test_a_503_raises_unavailable(serve: Callable[[Handler], None], code: int) -> None:
    serve(_status(code))
    with pytest.raises(ElasticsearchUnavailable) as raised:
        await ElasticsearchAdapter(URL).health()
    assert raised.value.status == code
    assert not isinstance(raised.value, ElasticsearchRejected)


@pytest.mark.asyncio
async def test_a_connection_error_raises_unavailable_without_a_status(
    serve: Callable[[Handler], None],
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    serve(refuse)
    with pytest.raises(ElasticsearchUnavailable) as raised:
        await ElasticsearchAdapter(URL).health()
    assert raised.value.status is None


@pytest.mark.asyncio
async def test_a_missing_alias_has_no_indices(serve: Callable[[Handler], None]) -> None:
    serve(_status(404, {"error": "alias [articles] missing", "status": 404}))
    assert await ElasticsearchAdapter(URL).alias_indices("articles") == []


@pytest.mark.asyncio
async def test_another_rejection_of_an_alias_lookup_is_raised(
    serve: Callable[[Handler], None],
) -> None:
    serve(_status(403, {"error": "forbidden", "status": 403}))
    with pytest.raises(ElasticsearchRejected) as raised:
        await ElasticsearchAdapter(URL).alias_indices("articles")
    assert raised.value.status == 403


@pytest.mark.asyncio
async def test_a_whole_bulk_400_raises_rejected(serve: Callable[[Handler], None]) -> None:
    serve(_status(400, PARSING))
    adapter = ElasticsearchAdapter(URL)
    (batch,) = adapter.build_batches("articles-test", [BulkDocument("a", 1, {"title": "x"})])
    with pytest.raises(ElasticsearchRejected) as raised:
        await adapter.bulk(batch)
    assert raised.value.status == 400
