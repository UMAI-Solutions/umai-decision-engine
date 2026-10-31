"""Suite `admission`: what key network restrictions add to admission (ADR-0046, AGENTS rule 6).

Admission runs in-process, without HTTP: `Admission.authenticate` plus the per-key rate limit,
as the engine API and the gateway call them for every request. Scenarios: a key without
networks (the baseline, with and without the failed-credential throttle), keys with one and
with 50 networks, the client address read behind a trusted proxy, and the refusals (403
`ip_not_allowed`, 401 counted, 429 throttled). Synthetic keys, documentation addresses
(RFC 5737, RFC 3849). No network.
"""

from __future__ import annotations

import argparse
import contextlib
import ipaddress
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from evals.common import report_dir, run_info
from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.keys import generate_key
from umai_decision_engine.tenancy.limits import FailedAuthThrottle, RateLimiter
from umai_decision_engine.tenancy.network import (
    client_address,
    parse_address,
    parse_networks,
    source_of,
)
from umai_decision_engine.tenancy.registry import KeyRegistry

NAME = "admission"
CALLS = 20_000  # per repeat
REPEATS = 7

FIFTY = [f"198.51.100.{4 * i}/30" for i in range(49)] + ["203.0.113.0/24"]  # match: the last


def _registry(entries: list[KeyRegistryEntry]) -> KeyRegistry:
    payload = (
        KeyRegistrySnapshot(tenant_id="tenant-bench", generated_at=datetime.now(UTC), keys=entries)
        .model_dump_json(exclude_none=True)
        .encode()
    )

    class Source:
        name = "memory"

        def fingerprint(self) -> str:
            return "bench"

        def scan(self) -> list[tuple[str, bytes]]:
            return [("memory/tenant-bench.json", payload)]

    registry = KeyRegistry(Source(), public_keys={}, require_signed=False)
    registry.reload(force=True)
    return registry


def _entry(key: Any, allowed_ips: list[str] | None) -> KeyRegistryEntry:
    return KeyRegistryEntry(
        key_id=key.key_id,
        secret_sha256=key.secret_sha256,
        env="live",
        region="sh",
        scopes=[Scope.EVALUATE],
        status="active",
        allowed_ips=allowed_ips,
    )


def _time(call) -> float:
    """Median over repeats of the mean microseconds per call."""

    per_call = []
    for _ in range(REPEATS):
        start = time.perf_counter()
        for _ in range(CALLS):
            call()
        per_call.append((time.perf_counter() - start) / CALLS * 1e6)
    return statistics.median(per_call)


def _refused(call):
    def run() -> None:
        with contextlib.suppress(HTTPException):
            call()

    return run


def run(args: argparse.Namespace) -> Path:
    info = run_info(NAME, "all", args.tag, Path(__file__))
    plain, one, fifty = (generate_key("live", "sh") for _ in range(3))
    registry = _registry(
        [_entry(plain, None), _entry(one, ["203.0.113.0/24"]), _entry(fifty, FIFTY)]
    )

    def admission(*, throttle: bool, trusted: tuple[str, ...] = ()) -> Admission:
        return Admission(
            shared_token=None,
            registry=registry,
            limiter=RateLimiter(1e9, 10**9),  # never refuses: measure its cost, not its effect
            development=False,
            trusted_proxies=parse_networks(trusted),
            throttle=FailedAuthThrottle(20, 60, max_sources=100_000) if throttle else None,
        )

    office, elsewhere = "203.0.113.10", "198.51.100.250"
    before, after = admission(throttle=False), admission(throttle=True)
    trusted = admission(throttle=True, trusted=("192.0.2.10/32",))
    blocked = admission(throttle=True)
    blocked.throttle = FailedAuthThrottle(20, 1e9, max_sources=100)  # no refill during the run
    for _ in range(20):
        blocked.throttle.failed(source_of(parse_address(elsewhere)))

    def before_adr(key: Any) -> Any:
        def call() -> None:  # no client address, no throttle: admission as it was
            before.admit(before.authenticate(f"Bearer {key.presentation}"))

        return call

    def admit(adm: Admission, key: Any, peer: str, forwarded: list[str] | None = None) -> Any:
        def call() -> None:  # what Admission.client_of and the dependency do per request
            client = client_address(peer, forwarded or [], adm.trusted_proxies)
            adm.admit(adm.authenticate(f"Bearer {key.presentation}", client=client))

        return call

    forged = "Bearer " + plain.presentation[:-6] + "AAAAAA"
    counting = admission(throttle=True)
    # A new source for every call (an attack from many addresses): 401, counted, never throttled;
    # past 100 000 sources the table evicts its oldest, which is measured too.
    sources = iter([str(ipaddress.IPv4Address(0x0A000000 + n)) for n in range(CALLS * REPEATS)])

    def bad_credential() -> None:
        with contextlib.suppress(HTTPException):
            peer = next(sources)
            counting.authenticate(forged, client=client_address(peer, [], counting.trusted_proxies))

    scenarios: list[tuple[str, Any]] = [
        ("before ADR-0046: key, no address, no throttle", before_adr(plain)),
        ("key without networks, peer address, throttle on", admit(after, plain, office)),
        (
            "key without networks, behind a trusted proxy (X-Forwarded-For with 2 hops)",
            admit(trusted, plain, "192.0.2.10", ["198.51.100.66, 203.0.113.10"]),
        ),
        ("key with 1 network, client inside", admit(after, one, office)),
        ("key with 50 networks, client in the last", admit(after, fifty, office)),
        (
            "403 ip_not_allowed (key with 50 networks, client outside)",
            _refused(admit(after, fifty, elsewhere)),
        ),
        ("401 bad credential, counted (a new source each time)", bad_credential),
        ("429 throttled source (before any lookup)", _refused(admit(blocked, plain, elsewhere))),
    ]
    rows = []
    baseline = None
    for label, call in scenarios:
        micros = _time(call)
        baseline = baseline if baseline is not None else micros
        rows.append(f"| {label} | {micros:.2f} | {micros - baseline:+.2f} |")

    lines = [
        f"# Suite `{NAME}` — {info.tag}",
        "",
        *info.header_lines(),
        "",
        f"In-process, no HTTP; {CALLS} calls × {REPEATS} repeats per scenario, median of the "
        "per-repeat mean. The admission path the engine API and the gateway run per request: the "
        "client address (`Admission.client_of`), `authenticate` and the per-key rate limit. "
        "The first row is admission before ADR-0046, without an address or a throttle.",
        "",
        "| Scenario | µs per call | vs. before |",
        "|---|---|---|",
        *rows,
        "",
    ]
    directory = report_dir(info)
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
