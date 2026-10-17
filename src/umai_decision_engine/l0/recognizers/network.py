"""IP addresses and unknown links (Lakera parity, docs/architecture/l0-entity-catalog.md).

IP: public addresses score high; private, CGNAT, unique-local and documentation ranges score
lower; loopback, link-local, multicast, unspecified, reserved and well-known public DNS
resolvers are not reported (as documented for Lakera, docs/reference/lakera-guard.md).

Unknown links: an http(s) / www URL whose registrable domain is not a known popular domain.
The engine ships a small curated list (``l0/data/popular_domains.txt``); a larger popularity
list can be supplied with ``UMAI_DE_POPULAR_DOMAINS_FILE`` (one domain per line). Guardrails add
their own trusted domains with ``l0.allowed_domains``, applied by the detector per request.
"""

from __future__ import annotations

import ipaddress
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from umai_decision_engine.l0.recognizers.base import Candidate, PatternSpec, RecognizerSpec

# ---------------------------------------------------------------- IP addresses

_IP_PUBLIC_SCORE = 0.8
_IP_INTERNAL_SCORE = 0.5  # private / CGNAT / unique-local / documentation: lower confidence

_WELL_KNOWN_RESOLVERS = frozenset(
    ipaddress.ip_address(a)
    for a in (
        "8.8.8.8",
        "8.8.4.4",
        "1.1.1.1",
        "1.0.0.1",
        "9.9.9.9",
        "149.112.112.112",
        "208.67.222.222",
        "208.67.220.220",
        "2001:4860:4860::8888",
        "2001:4860:4860::8844",
        "2606:4700:4700::1111",
        "2606:4700:4700::1001",
    )
)
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_DOCUMENTATION = tuple(
    ipaddress.ip_network(n)
    for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")
)


def _ip_score(text: str) -> float | None:
    """Score for an IP literal, or None when it is not reported."""

    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if ip in _WELL_KNOWN_RESOLVERS:
        return None
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return None
    if any(ip in net for net in _DOCUMENTATION if net.version == ip.version):
        return _IP_INTERNAL_SCORE
    if ip.version == 4 and ip in _CGNAT:
        return _IP_INTERNAL_SCORE
    if ip.is_reserved:  # 240.0.0.0/4 incl. 255.255.255.0 masks (Python also calls them private)
        return None
    if ip.is_private:  # RFC 1918, unique-local fc00::/7
        return _IP_INTERNAL_SCORE
    return _IP_PUBLIC_SCORE


def _refine_ip(fragment: str) -> list[Candidate]:
    value = fragment.rstrip(".:")
    score = _ip_score(value)
    return [] if score is None else [Candidate(0, len(value), True, score=score)]


_OCTET = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"

IP_ADDRESS = RecognizerSpec(
    entity="IP_ADDRESS",
    patterns=(
        PatternSpec(
            "ipv4",
            rf"(?<![0-9A-Za-z_.]){_OCTET}(?:\.{_OCTET}){{3}}(?![0-9A-Za-z_])(?!\.[0-9])",
            _IP_PUBLIC_SCORE,
        ),
        PatternSpec(
            "ipv6",
            r"(?<![0-9A-Za-z_:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Za-z_:])",
            _IP_PUBLIC_SCORE,
        ),
    ),
    canonical=lambda value: value.strip().lower(),
    refine=_refine_ip,
    context=("ip", "ip adresi", "ipv4", "ipv6", "sunucu", "server", "host", "adres"),
    context_boost=0.1,
    priority=3,
)

# ---------------------------------------------------------------- unknown links

_DATA = Path(__file__).resolve().parents[1] / "data"

# Two-label public suffixes we see in practice; a full public suffix list is not shipped.
_MULTI_LABEL_SUFFIXES = frozenset(
    {
        "com.tr",
        "net.tr",
        "org.tr",
        "gov.tr",
        "edu.tr",
        "gen.tr",
        "bel.tr",
        "k12.tr",
        "av.tr",
        "biz.tr",
        "info.tr",
        "web.tr",
        "tsk.tr",
        "pol.tr",
        "co.uk",
        "org.uk",
        "ac.uk",
        "gov.uk",
        "com.au",
        "co.jp",
        "co.kr",
        "com.br",
        "co.in",
        "com.cn",
        "co.nz",
        "com.mx",
        "co.za",
        "com.de",
        "co.de",
    }
)
# Public-sector suffixes treated as known (any domain under them).
_TRUSTED_SUFFIXES = frozenset(
    {
        "gov.tr",
        "edu.tr",
        "k12.tr",
        "bel.tr",
        "tsk.tr",
        "pol.tr",
        "gov.uk",
        "ac.uk",
        "europa.eu",
        "gov",
        "edu",
        "mil",
    }
)


def registrable_domain(host: str) -> str:
    labels = [label for label in host.lower().strip(".").split(".") if label]
    if len(labels) >= 3 and ".".join(labels[-2:]) in _MULTI_LABEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


@lru_cache(maxsize=1)
def popular_domains() -> frozenset[str]:
    domains: set[str] = set()
    sources = [_DATA / "popular_domains.txt"]
    extra = os.getenv("UMAI_DE_POPULAR_DOMAINS_FILE")
    if extra:
        sources.append(Path(extra))
    for source in sources:
        for line in source.read_text(encoding="utf-8").splitlines():
            entry = line.split("#", 1)[0].strip().lower()
            # Tranco-style "rank,domain" lines are accepted too.
            entry = entry.rsplit(",", 1)[-1].strip()
            if entry:
                domains.add(registrable_domain(entry))
    return frozenset(domains)


def url_domain(url: str) -> str | None:
    target = url if "://" in url else f"http://{url}"
    try:
        host = urlsplit(target).hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
        return None  # IP-literal URLs are reported as IP_ADDRESS
    except ValueError:
        return registrable_domain(host)


def domain_is_known(domain: str, extra: frozenset[str] = frozenset()) -> bool:
    if domain in popular_domains() or domain in extra:
        return True
    return any(domain == s or domain.endswith(f".{s}") for s in _TRUSTED_SUFFIXES)


_TRAILING = ".,;:!?)]}>'\"’”"


def _refine_link(fragment: str) -> list[Candidate]:
    value = fragment.rstrip(_TRAILING)
    domain = url_domain(value)
    if domain is None or domain_is_known(domain):
        return []
    return [Candidate(0, len(value), True)]


UNKNOWN_LINK = RecognizerSpec(
    entity="UNKNOWN_LINK",
    patterns=(PatternSpec("url", r"(?i)(?<![\w@./])(?:https?://|www\.)[^\s<>\"'`]{3,2048}", 0.7),),
    canonical=lambda value: value.strip().lower(),
    refine=_refine_link,
    priority=2,
)
