"""Network facts at admission (ADR-0046): the client's address, read through trusted proxies
only; a key's allowed networks; and the source a failed credential is counted against.

The client is the socket peer unless the peer is a trusted proxy (``UMAI_DE_TRUSTED_PROXIES``,
default none). Behind trusted proxies ``X-Forwarded-For`` is read from the right, skipping
trusted hops; the first untrusted hop is the client. An arbitrary ``X-Forwarded-For`` from an
untrusted peer is never read, and no other header (``Forwarded``, ``X-Real-IP``) is.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable, Sequence

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network

FORWARDED_FOR = "x-forwarded-for"


class NetworkSet:
    """Networks to test addresses against, kept as integer (network, mask) pairs per IP
    version: one AND and one comparison per network instead of ``ipaddress`` containment
    (ADR-0046 Validation: the admission benchmark)."""

    __slots__ = ("_v4", "_v6", "networks")

    def __init__(self, networks: Iterable[IPNetwork] = ()) -> None:
        self.networks = tuple(networks)
        self._v4 = tuple(
            (int(n.network_address), int(n.netmask)) for n in self.networks if n.version == 4
        )
        self._v6 = tuple(
            (int(n.network_address), int(n.netmask)) for n in self.networks if n.version == 6
        )

    def __contains__(self, address: IPAddress | None) -> bool:
        if address is None:  # an unknown client lies in no network
            return False
        value = int(address)
        for network, mask in self._v4 if address.version == 4 else self._v6:
            if value & mask == network:
                return True
        return False

    def __len__(self) -> int:
        return len(self.networks)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, NetworkSet) and self.networks == other.networks

    def __hash__(self) -> int:
        return hash(self.networks)

    def __repr__(self) -> str:
        return f"NetworkSet({[str(n) for n in self.networks]})"


def parse_networks(values: Iterable[str]) -> NetworkSet:
    """Networks from validated CIDR strings (the contract or the settings checked them)."""

    return NetworkSet(ipaddress.ip_network(value) for value in values)


def parse_address(value: str | None) -> IPAddress | None:
    """An address as a peer or a forwarded hop gives it; ``None`` when it is not one. Brackets
    and a port (``[2001:db8::1]:443``, ``203.0.113.7:5000``) are dropped; an IPv4-mapped IPv6
    address is its IPv4 address. Parsed with ``inet_pton``: a fifth of ``ip_address``'s cost
    on every request (the admission benchmark)."""

    if not value:
        return None
    text = value.strip()
    if text.startswith("["):
        text = text[1 : text.find("]")] if "]" in text else ""
    elif text.count(":") == 1:
        text = text.split(":", 1)[0]
    try:
        if ":" not in text:
            return ipaddress.IPv4Address(socket.inet_pton(socket.AF_INET, text))
        address = ipaddress.IPv6Address(socket.inet_pton(socket.AF_INET6, text))
    except (OSError, ValueError):
        return None
    mapped = address.ipv4_mapped
    return mapped if mapped is not None else address


def client_address(
    peer: str | None, forwarded_for: Sequence[str], trusted: NetworkSet
) -> IPAddress | None:
    """The client's address: the peer, or behind trusted proxies the rightmost untrusted hop of
    ``X-Forwarded-For`` (all its header lines, in order). ``None`` when it cannot be known: an
    unreadable peer, or an unreadable hop where the client should be."""

    address = parse_address(peer)
    if address not in trusted:
        return address
    hops = [hop for line in forwarded_for for hop in line.split(",") if hop.strip()]
    for hop in reversed(hops):
        address = parse_address(hop)
        if address not in trusted:
            return address
    return address  # every hop is a trusted proxy: the first one


def source_of(address: IPAddress | None) -> tuple[int, int]:
    """The key failed credentials are counted under: an IPv4 address, an IPv6 /64 (one
    subscriber's usual allocation, so rotating within it does not reset the count), or
    ``(0, 0)`` for an unknown client."""

    if address is None:
        return (0, 0)
    if address.version == 4:
        return (4, int(address))
    return (6, int(address) >> 64)


def describe_source(source: tuple[int, int]) -> str:
    """The source for a log line: the address or the /64."""

    version, value = source
    if version == 4:
        return str(ipaddress.IPv4Address(value))
    if version == 6:
        return f"{ipaddress.IPv6Address(value << 64)}/64"
    return "unknown"
