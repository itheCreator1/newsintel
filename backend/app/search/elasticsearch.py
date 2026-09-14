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
