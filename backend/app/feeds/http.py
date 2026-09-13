from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.core.config import Settings
from app.feeds.network import UnsafeFeedUrl, validate_public_url


class FeedTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class FeedHttpResponse:
    status_code: int
    content: bytes
    headers: httpx.Headers
    url: str


async def fetch_feed_http(
    url: str, validators: dict[str, str], settings: Settings
) -> FeedHttpResponse:
    current = url
    async with httpx.AsyncClient(
        timeout=settings.feed_timeout_seconds, follow_redirects=False
    ) as client:
        for redirect in range(settings.feed_redirect_limit + 1):
            destination = await validate_public_url(current, set(settings.feed_test_allowed_hosts))
            parsed = urlsplit(current)
            address = destination.addresses[0]
            pinned_host = f"[{address}]" if ":" in address else address
            pinned = urlunsplit(
                (
                    parsed.scheme,
                    f"{pinned_host}:{parsed.port}" if parsed.port else pinned_host,
                    parsed.path or "/",
                    parsed.query,
                    "",
                )
            )
            host_header = destination.hostname
            if parsed.port and parsed.port not in {80, 443}:
                host_header = f"{host_header}:{parsed.port}"
            headers = {"User-Agent": settings.feed_user_agent, "Host": host_header, **validators}
            async with client.stream(
                "GET", pinned, headers=headers, extensions={"sni_hostname": destination.hostname}
            ) as response:
                if response.status_code == 304:
                    return FeedHttpResponse(304, b"", response.headers, current)
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect == settings.feed_redirect_limit:
                        raise httpx.TooManyRedirects(
                            "Feed redirect limit exceeded", request=response.request
                        )
                    current = str(response.url.join(location)).replace(
                        str(response.url.netloc), parsed.netloc
                    )
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.feed_max_response_bytes:
                        raise FeedTooLarge("Feed response exceeds configured size limit")
                    chunks.append(chunk)
                return FeedHttpResponse(
                    response.status_code, b"".join(chunks), response.headers, current
                )
    raise UnsafeFeedUrl("Unreachable redirect state")


async def fetch_page_http(url: str, settings: Settings) -> FeedHttpResponse:
    """Fetch an article with the same DNS pinning and redirect validation as feeds."""
    current = url
    async with httpx.AsyncClient(
        timeout=settings.article_timeout_seconds, follow_redirects=False
    ) as client:
        for redirect in range(settings.article_redirect_limit + 1):
            destination = await validate_public_url(current, set(settings.feed_test_allowed_hosts))
            parsed = urlsplit(current)
            address = destination.addresses[0]
            pinned_host = f"[{address}]" if ":" in address else address
            pinned = urlunsplit(
                (
                    parsed.scheme,
                    f"{pinned_host}:{parsed.port}" if parsed.port else pinned_host,
                    parsed.path or "/",
                    parsed.query,
                    "",
                )
            )
            host_header = destination.hostname
            if parsed.port and parsed.port not in {80, 443}:
                host_header = f"{host_header}:{parsed.port}"
            headers = {"User-Agent": settings.feed_user_agent, "Host": host_header}
            async with client.stream(
                "GET", pinned, headers=headers, extensions={"sni_hostname": destination.hostname}
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location or redirect == settings.article_redirect_limit:
                        raise httpx.TooManyRedirects(
                            "Article redirect limit exceeded", request=response.request
                        )
                    current = str(response.url.join(location)).replace(
                        str(response.url.netloc), parsed.netloc
                    )
                    continue
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.article_max_response_bytes:
                        raise FeedTooLarge("Article response exceeds configured size limit")
                    chunks.append(chunk)
                return FeedHttpResponse(
                    response.status_code, b"".join(chunks), response.headers, current
                )
    raise UnsafeFeedUrl("Unreachable redirect state")
