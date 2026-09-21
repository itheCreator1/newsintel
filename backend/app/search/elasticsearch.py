import json
from dataclasses import dataclass
from typing import Any

import httpx


class ElasticsearchUnavailable(RuntimeError):
    pass


class OversizedDocument(ValueError):
    pass


@dataclass(frozen=True)
class BulkDocument:
    document_id: str
    revision: int
    source: dict[str, Any]


@dataclass(frozen=True)
class BulkBatch:
    documents: list[BulkDocument]
    body: bytes


@dataclass(frozen=True)
class BulkResult:
    document_id: str
    status: int
    error: str | None


class ElasticsearchAdapter:
    def __init__(
        self, base_url: str, *, max_documents: int = 100, max_bytes: int = 5 * 1024 * 1024
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.max_documents = max_documents
        self.max_bytes = max_bytes

    def _encoded(self, index_name: str, document: BulkDocument) -> bytes:
        action = {
            "index": {
                "_index": index_name,
                "_id": document.document_id,
                "version": document.revision,
                "version_type": "external_gte",
            }
        }
        return (
            json.dumps(action, separators=(",", ":"))
            + "\n"
            + json.dumps(document.source, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode()

    def build_batches(self, index_name: str, documents: list[BulkDocument]) -> list[BulkBatch]:
        batches: list[BulkBatch] = []
        current: list[BulkDocument] = []
        body = b""
        for document in documents:
            encoded = self._encoded(index_name, document)
            if len(encoded) > self.max_bytes:
                raise OversizedDocument(
                    f"document {document.document_id} exceeds {self.max_bytes} bytes"
                )
            if current and (
                len(current) == self.max_documents or len(body) + len(encoded) > self.max_bytes
            ):
                batches.append(BulkBatch(current, body))
                current, body = [], b""
            current.append(document)
            body += encoded
        if current:
            batches.append(BulkBatch(current, body))
        return batches

    async def bulk(self, batch: BulkBatch) -> list[BulkResult]:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
                response = await client.post(
                    "/_bulk", content=batch.body, headers={"content-type": "application/x-ndjson"}
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ElasticsearchUnavailable(str(exc)) from exc
        items = response.json().get("items", [])
        results: list[BulkResult] = []
        for document, item in zip(batch.documents, items, strict=True):
            result = item["index"]
            error = result.get("error")
            results.append(
                BulkResult(
                    document.document_id,
                    int(result["status"]),
                    json.dumps(error) if error else None,
                )
            )
        return results

    async def _request(
        self, method: str, path: str, *, json_body: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=30) as client:
                response = await client.request(method, path, json=json_body)
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            raise ElasticsearchUnavailable(str(exc)) from exc

    async def create_index(self, name: str, settings: dict[str, Any]) -> None:
        await self._request("PUT", f"/{name}", json_body=settings)

    async def refresh(self, name: str) -> None:
        await self._request("POST", f"/{name}/_refresh")

    async def alias_indices(self, alias: str) -> list[str]:
        try:
            response = await self._request("GET", f"/_alias/{alias}")
        except ElasticsearchUnavailable as exc:
            if "404" in str(exc):
                return []
            raise
        return list(response.json())

    async def switch_alias(self, alias: str, replacement: str, previous: list[str]) -> None:
        actions = [{"remove": {"index": name, "alias": alias}} for name in previous]
        actions.append({"add": {"index": replacement, "alias": alias}})
        await self._request("POST", "/_aliases", json_body={"actions": actions})

    async def open_point_in_time(self, alias: str) -> str:
        response = await self._request("POST", f"/{alias}/_pit?keep_alive=5m")
        return str(response.json()["id"])

    async def search(self, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", "/_search", json_body=body)
        result: dict[str, Any] = response.json()
        return result

    async def search_index(self, index_name: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._request("POST", f"/{index_name}/_search", json_body=body)
        result: dict[str, Any] = response.json()
        return result

    async def health(self) -> dict[str, Any]:
        result: dict[str, Any] = (await self._request("GET", "/_cluster/health")).json()
        return result

    async def index_stats(self, alias: str) -> tuple[int, int]:
        """Document count and store size in bytes over the indices behind `alias` (primaries)."""
        response = await self._request("GET", f"/{alias}/_stats/docs,store")
        total = response.json()["_all"]["primaries"]
        return int(total["docs"]["count"]), int(total["store"]["size_in_bytes"])
