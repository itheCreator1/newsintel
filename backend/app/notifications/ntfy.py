"""Publishing to ntfy (https://ntfy.sh or a self-hosted server) as JSON, so any language fits."""

from dataclasses import dataclass, field
from typing import Any

import httpx

TIMEOUT = httpx.Timeout(10.0)


class NtfyError(Exception):
    pass


@dataclass(frozen=True)
class Message:
    title: str
    body: str
    click: str | None = None
    tags: list[str] = field(default_factory=lambda: ["newspaper"])


async def publish(
    server: str,
    topic: str,
    token: str | None,
    message: Message,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    payload: dict[str, Any] = {"topic": topic, "title": message.title, "message": message.body}
    if message.click:
        payload["click"] = message.click
    payload["tags"] = message.tags
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    url = server if server.endswith("/") else f"{server}/"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise NtfyError(f"could not reach {server}: {exc}") from exc
    if response.status_code >= 400:
        raise NtfyError(f"ntfy answered {response.status_code}: {response.text[:300]}")
