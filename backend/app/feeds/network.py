import asyncio
import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit


class UnsafeFeedUrl(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedDestination:
    url: str
    hostname: str
    addresses: tuple[str, ...]


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not any(
        (ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast)
    )


async def validate_public_url(
    url: str, allowed_hosts: set[str] | None = None
) -> ValidatedDestination:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise UnsafeFeedUrl("Feed URL must be public HTTP(S) without credentials")
    hostname = parsed.hostname.casefold()
    if allowed_hosts and hostname in allowed_hosts:
        return ValidatedDestination(url, hostname, (hostname,))
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if not _is_public(str(literal)):
            raise UnsafeFeedUrl("Feed destination is not public")
        return ValidatedDestination(url, hostname, (str(literal),))
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise UnsafeFeedUrl("Feed hostname could not be resolved") from exc
    # IPv4 first: callers pin addresses[0], and Docker networks have no IPv6 route by default.
    addresses = tuple(
        sorted(
            {record[4][0] for record in records},
            key=lambda address: (ipaddress.ip_address(address).version, address),
        )
    )
    if not addresses or not all(_is_public(address) for address in addresses):
        raise UnsafeFeedUrl("Feed destination is not public")
    return ValidatedDestination(url, hostname, addresses)
