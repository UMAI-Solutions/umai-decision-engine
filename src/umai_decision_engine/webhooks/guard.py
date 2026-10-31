"""The SSRF guard for webhook URLs (ADR-0048 §8).

Registration: :func:`check_url` refuses what can never be a customer's public receiver. Delivery:
:class:`GuardedTransport` resolves the host itself, refuses every non-public address and connects
to the address it checked (TLS still verifies the host name), so a second DNS answer cannot point
the request at ``169.254.169.254`` or an internal service (DNS rebinding). No proxies from the
environment, no redirects (the sender never follows them)."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl
from collections.abc import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

import certifi
import httpcore
import httpx

Address = ipaddress.IPv4Address | ipaddress.IPv6Address
# host, port → the addresses it resolves to (strings, as getaddrinfo answers)
Resolver = Callable[[str, int], Awaitable[list[str]]]

SLACK_HOST = "hooks.slack.com"
SLACK_PATH = "/services/"

# Global by the standard library's reading, yet never a customer's receiver: the cloud hosts'
# metadata and platform addresses, and IPv6 forms that carry an IPv4 address inside.
_REFUSED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "168.63.129.16/32",  # Azure platform (wire server, DNS, health probes)
        "169.254.169.254/32",  # instance metadata (Azure, AWS, GCP)
        "100.100.100.200/32",  # Alibaba Cloud metadata
        "fd00:ec2::254/128",  # AWS metadata over IPv6
        "64:ff9b::/96",  # NAT64
        "64:ff9b:1::/48",  # local-use NAT64
        "2002::/16",  # 6to4
        "2001::/32",  # Teredo
    )
)
_REFUSED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa", ".intranet")


class InvalidUrl(ValueError):
    """The URL can never be a webhook target; the message says why, in plain words."""


class BlockedAddress(Exception):
    """The host resolves to an address webhooks may not reach."""


def address_allowed(address: Address) -> bool:
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if any(address in network for network in _REFUSED_NETWORKS):
        return False
    return address.is_global and not address.is_multicast


def _literal(host: str) -> Address | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None


def check_url(url: str, kind: str) -> str:
    """The URL as given, when it may be registered; :class:`InvalidUrl` otherwise. Resolution is
    checked separately (:func:`resolve_public`)."""

    if any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        raise InvalidUrl("the URL contains spaces or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise InvalidUrl("not a valid URL") from exc
    if parts.scheme != "https":
        raise InvalidUrl("the URL must start with https://")
    if "@" in parts.netloc:
        raise InvalidUrl("the URL must not contain a user name or password")
    if parts.fragment or url.endswith("#"):
        raise InvalidUrl("the URL must not contain a fragment (#…)")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise InvalidUrl("the URL has no host")
    if port is not None and port != 443 and port < 1024:
        raise InvalidUrl("the port must be 443 or between 1024 and 65535")
    literal = _literal(host)
    if literal is not None:
        if not address_allowed(literal):
            raise InvalidUrl("the host is not a public address")
    elif host == "localhost" or host.endswith(_REFUSED_SUFFIXES) or "." not in host:
        raise InvalidUrl("the host is not a public name")
    if kind == "slack" and (
        host != SLACK_HOST or port is not None or not parts.path.startswith(SLACK_PATH)
    ):
        raise InvalidUrl(
            "a Slack endpoint takes a Slack incoming-webhook URL (https://hooks.slack.com/services/…)"
        )
    return url


async def system_resolver(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def resolve_public(
    host: str, port: int, resolver: Resolver, *, timeout_s: float
) -> list[str]:
    """The host's addresses when **all** of them are public; :class:`BlockedAddress` when any is
    not (a round-robin answer must not reach an internal address now and then). DNS failures
    raise ``OSError``."""

    literal = _literal(host)
    if literal is not None:
        if not address_allowed(literal):
            raise BlockedAddress(host)
        return [str(literal)]
    async with asyncio.timeout(timeout_s):
        answers = await resolver(host, port)
    addresses = [_literal(a) for a in answers]
    if not addresses:
        raise OSError(f"{host} has no addresses")
    if any(a is None or not address_allowed(a) for a in addresses):
        raise BlockedAddress(host)
    return [str(a) for a in addresses]


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """Resolves and checks every connection's host, then connects to a checked address."""

    def __init__(self, resolver: Resolver, *, timeout_s: float) -> None:
        self._resolver = resolver
        self._timeout_s = timeout_s
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable | None = None,
    ) -> httpcore.AsyncNetworkStream:
        addresses = await resolve_public(host, port, self._resolver, timeout_s=self._timeout_s)
        error: Exception | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                error = exc
        assert error is not None
        raise error

    async def connect_unix_socket(self, *args, **kwargs) -> httpcore.AsyncNetworkStream:
        raise BlockedAddress("unix sockets are never a webhook target")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class GuardedTransport(httpx.AsyncHTTPTransport):
    """httpx's transport with the guarded backend under its connection pool and no proxy from
    the environment. Replacing ``_pool`` is the one way httpx 0.28 lets a backend in; the tests
    exercise it so an httpx upgrade that changes it fails loudly."""

    def __init__(self, resolver: Resolver = system_resolver, *, connect_timeout_s: float) -> None:
        super().__init__(trust_env=False)
        context = ssl.create_default_context(cafile=certifi.where())
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=context,
            network_backend=GuardedBackend(resolver, timeout_s=connect_timeout_s),
            max_connections=50,
            keepalive_expiry=5.0,
        )
