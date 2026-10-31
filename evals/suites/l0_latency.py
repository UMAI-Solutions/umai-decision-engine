"""Suite `l0-latency`: what L0 and admission cost per request (launch plan S0-15; ADR-0019,
ADR-0023, ADR-0043, ADR-0046 ask for admission to be reported here).

In-process, no HTTP, one thread, timed call by call (``perf_counter_ns``), percentiles over
``CALLS`` calls after a warm-up.

- **Admission**, step by step and as a whole, as ``POST /v1/evaluate`` runs it: the client
  address, the failed-credential throttle (ADR-0046 D3), the key lookup with its constant-time
  hash compare and expiry (ADR-0023), the key's allowed networks (ADR-0046 D1; 1 and 50
  networks), the per-key rate limit, scope and tenant binding, the prompt-size check (ADR-0019
  §4) and the monthly quota (ADR-0043). Synthetic keys, documentation addresses.
- **L0**, the demo guardrail's active ``l0`` block (every catalog type it names) over synthetic
  Turkish business text with a block of identifiers about every 360 characters, at 500, 4 000
  and 16 000 characters; every text is unique, so nothing is cached.
"""

from __future__ import annotations

import argparse
import os
import statistics
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.common import EVAL_HMAC_KEY, ROOT, report_dir, run_info
from evals.stack import host_busy, host_cpu_times, performance_cpus, set_affinity
from umai_decision_engine.contracts.credentials import (
    KeyRegistryEntry,
    KeyRegistrySnapshot,
    Scope,
)
from umai_decision_engine.contracts.entitlements import TenantEntitlements
from umai_decision_engine.contracts.evaluation import ChatMessage, Content, input_size
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.policy.store import parse_bundle
from umai_decision_engine.tenancy.admission import Admission
from umai_decision_engine.tenancy.keys import generate_key, hashes_match, parse_key
from umai_decision_engine.tenancy.limits import FailedAuthThrottle, RateLimiter
from umai_decision_engine.tenancy.network import client_address, parse_address, source_of
from umai_decision_engine.tenancy.quota import QuotaCounter
from umai_decision_engine.tenancy.registry import KeyRegistry

NAME = "l0-latency"
CALLS = 20_000
L0_RUNS = 200
SIZES = (500, 4_000, 16_000)
BUNDLE = "gr-demo-data-protection"
TENANT = "tenant-bench"
ADMISSION_BUDGET_US = 1_000  # p99, docs/architecture/decision-merge-and-failure.md (Budgets)
L0_BUDGET_MS = 20.0  # p95 at 4 000 characters, docs/architecture/l0-entity-detection.md
FIFTY = [f"198.51.100.{4 * i}/30" for i in range(49)] + ["203.0.113.0/24"]  # match: the last

BUSINESS = (
    "Toplantıda bütçe, teslim tarihleri ve tedarikçi sözleşmeleri konuşuldu; ekip bir sonraki "
    "adımları netleştirdi ve raporu yönetime iletti. "
)
IDENTIFIERS = (
    "Müşteri Ayşe Demir, TC {tckn}, telefon 0532 {a} {b} {c}, e-posta ayse.{n}@example.com, "
    "IBAN TR33 0006 1005 1978 6457 8413 26. "
)
TCKN = ["10000000146", "10000000078", "10000000214", "10000000282", "10000000350"]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--cpu",
        default="auto",
        help="l0-latency: logical CPU to pin to (auto: a performance core; none: no pinning)",
    )


def _registry(entries: list[KeyRegistryEntry]) -> KeyRegistry:
    snapshot = KeyRegistrySnapshot(
        tenant_id=TENANT,
        generated_at=datetime.now(UTC),
        keys=entries,
        entitlements=TenantEntitlements(
            edition="community",
            monthly_requests=1_000_000_000,
            monthly_test_requests=1_000_000,
            max_input_chars=32_000,
        ),
    )
    payload = snapshot.model_dump_json(exclude_none=True).encode()

    class Source:
        name = "memory"

        def fingerprint(self) -> str:
            return "bench"

        def scan(self) -> list[tuple[str, bytes]]:
            return [(f"memory/{TENANT}.json", payload)]

    registry = KeyRegistry(Source(), public_keys={}, require_signed=False)
    registry.reload(force=True)
    return registry


def _entry(key: Any, allowed_ips: list[str] | None) -> KeyRegistryEntry:
    return KeyRegistryEntry(
        key_id=key.key_id,
        secret_sha256=key.secret_sha256,
        env="live",
        region="eu",
        scopes=[Scope.EVALUATE, Scope.GATEWAY],
        status="active",
        allowed_ips=allowed_ips,
    )


def _percentiles_us(call: Callable[[], object], calls: int = CALLS) -> tuple[float, float, float]:
    for _ in range(min(2_000, calls)):
        call()
    samples = []
    clock = time.perf_counter_ns
    for _ in range(calls):
        start = clock()
        call()
        samples.append((clock() - start) / 1000)
    q = statistics.quantiles(samples, n=100, method="inclusive")
    return q[49], q[94], q[98]


def _admission_rows() -> tuple[list[str], float]:
    plain, one, fifty = (generate_key("live", "eu") for _ in range(3))
    registry = _registry(
        [_entry(plain, None), _entry(one, ["203.0.113.0/24"]), _entry(fifty, FIFTY)]
    )
    admission = Admission(
        shared_token=None,
        registry=registry,
        limiter=RateLimiter(1e9, 10**9),  # never refuses: its cost, not its effect
        development=False,
        quota=QuotaCounter(None),
        throttle=FailedAuthThrottle(20, 60, max_sources=100_000),
    )
    office = parse_address("203.0.113.10")
    source = source_of(office)
    parsed = parse_key(plain.presentation)
    assert parsed is not None
    stored = registry.lookup(parsed.key_id)
    one_networks = registry.lookup(one.key_id).networks
    fifty_networks = registry.lookup(fifty.key_id).networks
    now = datetime.now(UTC)
    content = Content(
        messages=[ChatMessage(role="user", content=BUSINESS * 30)], focus="LAST_USER_MESSAGE"
    )

    def lookup() -> None:  # what _resolve does for a key without networks
        key = parse_key(plain.presentation)
        found = registry.lookup(key.key_id)
        hashes_match(key.secret_sha256, found.entry.secret_sha256)
        found.usable_at(now)

    def whole(key: Any) -> Callable[[], None]:
        presented = f"Bearer {key.presentation}"

        def call() -> None:  # the order of POST /v1/evaluate
            client = client_address("203.0.113.10", [], admission.trusted_proxies)
            principal = admission.authenticate(presented, client=client)
            admission.admit(principal)
            if Scope.EVALUATE not in principal.scopes:
                raise AssertionError
            tenant = principal.bind_tenant(None)
            if input_size(content) > admission.max_input_chars(tenant, 32_000):
                raise AssertionError
            admission.admit_quota(tenant, principal.env)

        return call

    steps: list[tuple[str, Callable[[], object]]] = [
        (
            "client address (socket peer, no trusted proxy)",
            lambda: client_address("203.0.113.10", [], admission.trusted_proxies),
        ),
        (
            "failed-credential throttle check (ADR-0046 D3)",
            lambda: admission.throttle.blocked(source),
        ),
        ("key lookup: parse, registry, constant-time hash, expiry (ADR-0023)", lookup),
        ("allowed networks, 1 network (ADR-0046 D1)", lambda: office in one_networks),
        ("allowed networks, 50 networks, match in the last", lambda: office in fifty_networks),
        ("per-key rate limit (token bucket)", lambda: admission.limiter.acquire("key:bench")),
        ("scope and tenant binding", lambda: Scope.EVALUATE in frozenset(stored.entry.scopes)),
        (
            "prompt size: limit and characters of a 4 000-char message (ADR-0019 §4)",
            lambda: input_size(content) <= admission.max_input_chars(TENANT, 32_000),
        ),
        ("monthly quota count (ADR-0043)", lambda: admission.admit_quota(TENANT, "live")),
        ("**whole admission**, key without networks", whole(plain)),
        ("**whole admission**, key with 50 networks", whole(fifty)),
    ]
    rows, worst = [], 0.0
    for label, call in steps:
        p50, p95, p99 = _percentiles_us(call)
        if label.startswith("**whole"):
            worst = max(worst, p99)
        rows.append(f"| {label} | {p50:.1f} | {p95:.1f} | {p99:.1f} |")
    return rows, worst


def _text(chars: int, n: int) -> str:
    parts, i = [f"Kayıt {n}. "], 0
    while sum(map(len, parts)) < chars:
        parts.append(BUSINESS * 2)
        parts.append(
            IDENTIFIERS.format(
                tckn=TCKN[(n + i) % len(TCKN)],
                a=f"{(n + i) % 900 + 100}",
                b=f"{(n * 7 + i) % 90 + 10}",
                c=f"{(n * 3 + i) % 90 + 10}",
                n=n * 100 + i,
            )
        )
        i += 1
    return "".join(parts)[:chars]


def _l0_rows() -> tuple[list[str], dict[int, tuple[float, float, float]], str]:
    active = (ROOT / "policies" / BUNDLE / "active").read_text(encoding="utf-8").strip()
    bundle = parse_bundle((ROOT / "policies" / BUNDLE / active / "bundle.yaml").read_bytes())
    detector = EntityDetector(lambda _tenant: EVAL_HMAC_KEY, threads=1, cache_entries=0)
    detector.warm_up()
    results: dict[int, tuple[float, float, float]] = {}
    rows = []
    try:
        for size in SIZES:
            texts = [_text(size, n) for n in range(L0_RUNS + 5)]
            for text in texts[:5]:
                detector.detect(text, tenant_id=TENANT, config=bundle.l0)
            samples = []
            found = 0
            for text in texts[5:]:
                start = time.perf_counter_ns()
                result = detector.detect(text, tenant_id=TENANT, config=bundle.l0)
                samples.append((time.perf_counter_ns() - start) / 1e6)
                found += len(result.entities)
            q = statistics.quantiles(samples, n=100, method="inclusive")
            results[size] = (q[49], q[94], q[98])
            rows.append(
                f"| {size:,} chars | {q[49]:.2f} ms | {q[94]:.2f} ms | {q[98]:.2f} ms | "
                f"{found / L0_RUNS:.0f} |".replace(",", " ")
            )
    finally:
        detector.close()
    types = len(bundle.l0.entities)  # derived from the policies (policy-bundle 0.6)
    return rows, results, f"`{bundle.id}` v{bundle.version}, {types} entity types"


def run(args: argparse.Namespace) -> Path:
    info = run_info(NAME, "all", args.tag, Path(__file__))
    cpu = {"none": None, "auto": performance_cpus()[-1]}.get(args.cpu)
    if args.cpu not in ("none", "auto"):
        cpu = int(args.cpu)
    pinned = cpu is not None and set_affinity(os.getpid(), [cpu])
    where = f"pinned to logical CPU {cpu}" if pinned else "not pinned"
    host_before = host_cpu_times()
    admission_rows, admission_p99 = _admission_rows()
    l0_rows, l0, config = _l0_rows()
    busy = host_busy(host_before, host_cpu_times())
    p95_4k = l0[4_000][1]
    share = admission_p99 / 1000 / l0[4_000][0] * 100
    verdict = "PASS" if admission_p99 <= ADMISSION_BUDGET_US else "FAIL"
    l0_note = "within" if p95_4k <= L0_BUDGET_MS else "above"
    lines = [
        f"# Suite `{NAME}` — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Method:** in-process, one thread, each call timed; admission {CALLS} calls per "
        f"row after a warm-up, L0 {L0_RUNS} unique texts per size after 5 warm-up texts",
        f"- **CPU:** {where}; the host was {busy:.0%} busy during the run (other work competes "
        "for caches, memory bandwidth and turbo headroom; tail percentiles include preemption)",
        f"- **L0 configuration:** {config} (the active version's `l0` block), NER off, "
        "L0 cache off",
        "",
        f"## Verdict (admission, launch plan S0-15): {verdict}",
        "",
        f"- Admission p99 {admission_p99:.1f} µs; budget {ADMISSION_BUDGET_US} µs "
        f"({share:.2f} % of an L0 scan of 4 000 characters at p50).",
        f"- L0 p95 at 4 000 characters {p95_4k:.2f} ms, {l0_note} the {L0_BUDGET_MS:g} ms "
        "hypothesis; L0 is judged on the reference hardware and a quiet host, not here.",
        "",
        "## Admission per request (µs)",
        "",
        "Each step alone, then the whole sequence of `POST /v1/evaluate` (client address → "
        "throttle → key → networks → rate limit → scope and tenant → prompt size → quota); the "
        "gateway runs the same steps. No I/O: keys, networks and limits come from the signed "
        "snapshot in memory, the quota from an in-memory count refreshed off the request path.",
        "",
        "| Step | p50 | p95 | p99 |",
        "|---|---|---|---|",
        *admission_rows,
        "",
        "## L0 entity detection",
        "",
        "| Input | p50 | p95 | p99 | Entities found per text |",
        "|---|---|---|---|---|",
        *l0_rows,
        "",
        "**Reading.** Admission costs microseconds and does not grow with the input; L0 grows "
        "about linearly with the text. Measured on a developer laptop: the reference hardware "
        "(Q1) and a B2s vCPU are slower by a factor to be measured there; the budgets hold with "
        "a wide margin for admission. Not measured here: HTTP and JSON handling (see the `load` "
        "suite), concurrency.",
        "",
    ]
    directory = report_dir(info)
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
