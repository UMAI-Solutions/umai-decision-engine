"""Suite `load`: throughput and latency under concurrent load (launch plan S0-3), and what a
B2s VM (2 vCPU, 4 GiB) can carry.

Scenarios, each a stream of unique 1 000-character synthetic Turkish texts (so the L0 cache of
ADR-0037 never answers):

- ``evaluate-l0``: ``POST /v1/evaluate`` with the demo guardrail's active version (L0 only);
- ``evaluate-l1b``: ``POST /v1/evaluate`` with its L1b version against a stub OpenAI Decisions
  provider: two policies enforce (one call on the request path), seven in shadow (a second call
  after the answer), as umai-beta runs its customer's guardrail;
- ``gateway``: ``POST /v1/chat/completions`` through the gateway to a stub upstream (provider key
  passed through), input and answer checked.

Local mode (default) starts the stack of ``evals/stack.py`` (production settings) and pins the
policy service, the engine and the gateway to ``--server-cpus`` (default: two logical CPUs, one
physical core, as the two vCPUs of a B2s); this process and the stubs run on other CPUs. Closed
loop: ``--concurrency`` workers each send the next request when the previous one answered, for
``--duration-s`` after ``--warmup-s``. The server's CPU time per request is read from the
process, which is what the B2s estimate scales.

Remote mode (``--engine-url`` / ``--gateway-url``, the test tenant's keys comma-separated in
``UMAI_LOAD_API_KEYS``, used in turn so each stays under its rate limit, provider key in
``UMAI_LOAD_PROVIDER_KEY``) sends the same requests to a deployment; ``--rates`` switches to an
open loop at fixed arrival rates, which is what the beta plan uses
(``docs/workflow/load-test-beta.md``). Never point it at a deployment without the operator's
go-ahead.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import statistics
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from evals.common import report_dir, run_info
from evals.stack import (
    APPLICATION,
    DEMO_BUNDLE,
    L1B_BUNDLE,
    L1B_ENFORCED,
    L1B_TIMEOUT_MS,
    PROVIDER_KEY,
    LocalStack,
    host_busy,
    host_cpu_times,
    own_cpu_seconds,
    performance_cpus,
)
from evals.suites.revocation import evaluate_body

NAME = "load"
SCENARIOS = ("evaluate-l0", "evaluate-l1b", "gateway")
SERVICE = {"evaluate-l0": "engine", "evaluate-l1b": "engine", "gateway": "gateway"}

SENTENCES = [
    "Toplantıda bütçe, teslim tarihleri ve tedarikçi sözleşmeleri konuşuldu.",
    "Ekip bir sonraki adımları netleştirdi ve sorumlulukları yeniden dağıttı.",
    "Satış raporundaki bölgesel farklar bir sonraki çeyrekte yeniden incelenecek.",
    "Müşteri memnuniyeti anketinin sonuçları yönetim kuruluna sunulacak.",
    "Yeni ürün lansmanı için pazarlama planının taslağı hazırlandı.",
    "Please summarise the open action items and the decisions of the meeting.",
    "Yazılım ekibi test ortamındaki performans sorunlarını cuma gününe kadar çözecek.",
    "İnsan kaynakları yıllık eğitim takvimini tüm departmanlarla paylaştı.",
]
WORDS = [
    *("kırmızı", "mavi", "yeşil", "sarı", "turuncu", "mor", "beyaz", "gri", "lacivert", "bordo"),
    *("zeytin", "kahve", "elma", "armut", "kiraz", "erik", "ayva", "incir", "üzüm", "nar"),
    *("karpuz", "kavun", "şeftali", "vişne", "dağ", "nehir", "deniz", "göl", "orman", "ova"),
    *("vadi", "tepe", "ada", "kıyı", "çöl", "yayla"),
]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scenarios", default=",".join(SCENARIOS), help="load: comma-separated")
    parser.add_argument("--concurrency", default="1,4,16,32", help="load: closed-loop workers")
    parser.add_argument("--rates", help="load: open loop, requests/s per step (remote mode)")
    parser.add_argument("--duration-s", type=float, default=20.0, help="load: per step")
    parser.add_argument("--warmup-s", type=float, default=3.0, help="load: per step, not counted")
    parser.add_argument("--chars", type=int, default=1000, help="load: characters per message")
    parser.add_argument(
        "--server-cpus", default="0,1", help="load: logical CPUs for the services, or 'all'"
    )
    parser.add_argument("--stub-latency-ms", type=float, default=0.0, help="load: stub answers")
    parser.add_argument("--engine-url", help="load: remote engine API base URL")
    parser.add_argument("--gateway-url", help="load: remote gateway base URL")
    parser.add_argument("--application", default=APPLICATION, help="load: gateway application")
    parser.add_argument("--bundle-l0", default=DEMO_BUNDLE, help="load: L0-only guardrail")
    parser.add_argument("--bundle-l1b", default=L1B_BUNDLE, help="load: L1b guardrail")


@dataclass
class Target:
    engine: str | None
    gateway: str | None
    keys: list[str]
    provider_key: str
    application: str
    bundles: dict[str, str]
    texts: Texts


@dataclass
class Step:
    scenario: str
    load: str  # "c=16" or "r=20/s"
    duration_s: float
    latencies_ms: list[float] = field(default_factory=list)
    statuses: Counter[int] = field(default_factory=Counter)
    failures: Counter[str] = field(default_factory=Counter)
    server_cpu_s: float | None = None
    client_cpu_s: float = 0.0
    host_busy: float = float("nan")  # the whole host, this test included
    rss: dict[str, int] = field(default_factory=dict)
    # 2xx evaluations decided by a failure policy (degraded or errors, e.g. an L1b call that ran
    # past its timeout while the process was saturated): answered, but not as the policy says
    degraded: int = 0
    actions: Counter[str] = field(default_factory=Counter)

    def record(self, outcome: tuple[int | None, str | None, str | None], latency_ms: float) -> None:
        status, failure, action = outcome
        self.latencies_ms.append(latency_ms)
        if status is None:
            self.failures[failure or "error"] += 1
            return
        self.statuses[status] += 1
        if action is not None:
            self.actions[action.removesuffix("!")] += 1
            self.degraded += action.endswith("!")

    @property
    def ok(self) -> int:
        return sum(n for status, n in self.statuses.items() if 200 <= status < 300)

    def percentiles(self) -> tuple[float, float, float]:
        if len(self.latencies_ms) < 2:
            value = self.latencies_ms[0] if self.latencies_ms else float("nan")
            return value, value, value
        q = statistics.quantiles(self.latencies_ms, n=100, method="inclusive")
        return q[49], q[94], q[98]


class Texts:
    """Unique synthetic texts: a three-word tag (~33 000 combinations) and shuffled sentences."""

    def __init__(self, chars: int, seed: int = 7) -> None:
        self.chars = chars
        self.random = random.Random(seed)
        self.count = 0

    def next(self) -> str:
        self.count += 1
        tag = " ".join(self.random.choice(WORDS) for _ in range(3))
        parts = [f"Not {tag}:"]
        while len(" ".join(parts)) < self.chars:
            parts.append(self.random.choice(SENTENCES))
        return " ".join(parts)[: self.chars]


def _request(target: Target, scenario: str, text: str, n: int) -> tuple[str, dict, dict]:
    headers = {"Authorization": f"Bearer {target.keys[n % len(target.keys)]}"}
    if scenario == "gateway":
        headers |= {
            "X-Umai-Application": target.application,
            "X-Umai-Provider-Key": target.provider_key,
        }
        body = {"model": "stub-model", "messages": [{"role": "user", "content": text}]}
        return f"{target.gateway}/v1/chat/completions", body, headers
    bundle = target.bundles[scenario]
    return f"{target.engine}/v1/evaluate", evaluate_body(text, f"load-{n}", bundle), headers


async def _send(
    client: httpx.AsyncClient, target: Target, scenario: str
) -> tuple[int | None, str | None, str | None]:
    """(status, transport failure, decision action of an evaluation; ``!`` marks degraded)."""

    url, body, headers = _request(target, scenario, target.texts.next(), target.texts.count)
    try:
        response = await client.post(url, json=body, headers=headers)
    except httpx.HTTPError as exc:
        return None, type(exc).__name__, None
    action = None
    if scenario != "gateway" and response.status_code == 200:
        answer = response.json()
        action = str(answer.get("decision", {}).get("action"))
        if answer.get("degraded") or answer.get("errors"):
            action += "!"
    return response.status_code, None, action


async def _closed(target: Target, scenario: str, workers: int, warmup: float, duration: float):
    step = Step(scenario, f"c={workers}", duration)
    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        start = time.perf_counter()
        end_warmup, end = start + warmup, start + warmup + duration

        async def worker() -> None:
            while (now := time.perf_counter()) < end:
                outcome = await _send(client, target, scenario)
                done = time.perf_counter()
                if now >= end_warmup and done <= end:
                    step.record(outcome, (done - now) * 1000)

        await asyncio.gather(*(worker() for _ in range(workers)))
    return step


async def _open(target: Target, scenario: str, rate: float, warmup: float, duration: float):
    """Fixed arrival rate; latency from the scheduled start, so a slow server is not hidden by
    the generator waiting for it (no coordinated omission). At most 512 calls in flight."""

    step = Step(scenario, f"r={rate:g}/s", duration)
    gate = asyncio.Semaphore(512)
    limits = httpx.Limits(max_connections=512, max_keepalive_connections=64)
    async with httpx.AsyncClient(timeout=60, limits=limits) as client:
        start = time.perf_counter()
        end_warmup = start + warmup
        tasks = []

        async def one(scheduled: float) -> None:
            async with gate:
                outcome = await _send(client, target, scenario)
            if scheduled >= end_warmup:
                step.record(outcome, (time.perf_counter() - scheduled) * 1000)

        n = 0
        while (scheduled := start + n / rate) < start + warmup + duration:
            delay = scheduled - time.perf_counter()
            if delay > 0:
                await asyncio.sleep(delay)
            tasks.append(asyncio.create_task(one(scheduled)))
            n += 1
        await asyncio.gather(*tasks)
    return step


def _run_step(stack: LocalStack | None, target: Target, scenario: str, load: str, args) -> Step:
    service = stack.services.get(SERVICE[scenario]) if stack is not None else None
    before = service.stats() if service is not None else None
    client_before = own_cpu_seconds()
    host_before = host_cpu_times()
    measured_from = time.perf_counter()
    if load.startswith("r"):
        coroutine = _open(target, scenario, float(load[1:]), args.warmup_s, args.duration_s)
    else:
        coroutine = _closed(target, scenario, int(load[1:]), args.warmup_s, args.duration_s)
    step = asyncio.run(coroutine)
    elapsed = time.perf_counter() - measured_from
    step.host_busy = host_busy(host_before, host_cpu_times())
    step.client_cpu_s = (own_cpu_seconds() - client_before) * args.duration_s / elapsed
    after = service.stats() if service is not None else None
    if before is not None and after is not None:
        # CPU over warm-up and measurement, scaled to the measured window
        step.server_cpu_s = (after[0] - before[0]) * args.duration_s / elapsed
    if stack is not None:
        step.rss = {n: s.stats()[1] for n, s in stack.services.items() if s.stats() is not None}
    return step


def _ms(value: float) -> str:
    return f"{value:.1f}" if value < 100 else f"{value:.0f}"


def _table(steps: list[Step]) -> list[str]:
    rows = [
        "| Scenario | Load | Requests | Non-2xx | Degraded | Throughput | p50 ms | p95 ms | "
        "p99 ms | Server CPU | CPU ms/request | Client CPU | Host busy |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for step in steps:
        p50, p95, p99 = step.percentiles()
        total = sum(step.statuses.values()) + sum(step.failures.values())
        bad = total - step.ok
        cpu = "—" if step.server_cpu_s is None else f"{step.server_cpu_s / step.duration_s:.0%}"
        per = "—"
        if step.server_cpu_s is not None and step.ok:
            per = f"{step.server_cpu_s / step.ok * 1000:.2f}"
        rows.append(
            f"| {step.scenario} | {step.load} | {total} | {bad} | "
            f"{step.degraded if step.actions else '—'} | {step.ok / step.duration_s:.0f}/s"
            f" | {_ms(p50)} | {_ms(p95)} | {_ms(p99)} | {cpu} | {per} | "
            f"{step.client_cpu_s / step.duration_s:.0%} | {step.host_busy:.0%} |"
        )
    return rows


def _failures(steps: list[Step]) -> list[str]:
    lines = []
    for step in steps:
        odd = {str(k): v for k, v in step.statuses.items() if not 200 <= k < 300}
        odd |= dict(step.failures)
        if odd:
            lines.append(f"- {step.scenario} {step.load}: {odd}")
        if step.degraded:
            lines.append(
                f"- {step.scenario} {step.load}: {step.degraded} degraded answers; decisions "
                f"{dict(step.actions)}"
            )
    return lines or ["None."]


def _best(steps: list[Step], scenario: str) -> Step | None:
    """The highest throughput with every answer as the policy says: no non-2xx, nothing
    decided by a failure policy (that is overload, not capacity)."""

    candidates = [s for s in steps if s.scenario == scenario and s.ok]
    clean = [
        s
        for s in candidates
        if not s.degraded and not s.failures and s.ok == sum(s.statuses.values())
    ]
    candidates = clean or candidates
    return max(candidates, key=lambda s: s.ok / s.duration_s) if candidates else None


def _estimate(steps: list[Step], scenarios: list[str]) -> list[str]:
    """B2s: 2 vCPU when credits allow, 20 % of the VM (0.4 vCPU) sustained
    (docs/reference/azure-b-series.md). One uvicorn process runs Python on one core at a time,
    so a process is capped at one vCPU whatever the VM has."""

    lines = [
        "| Scenario | Local peak, no degraded answer (2 logical CPUs) | CPU ms/request | "
        "One process, 1 vCPU (burst), host 1.5–2.5× slower | "
        "B2s sustained (0.4 vCPU for the whole VM) |",
        "|---|---|---|---|---|",
    ]
    for scenario in scenarios:
        best = _best(steps, scenario)
        if best is None or best.server_cpu_s is None:
            continue
        per_ms = best.server_cpu_s / best.ok * 1000
        peak = best.ok / best.duration_s
        burst = (1000 / (per_ms * 2.5), 1000 / (per_ms * 1.5))
        sustained = (400 / (per_ms * 2.5), 400 / (per_ms * 1.5))
        lines.append(
            f"| {scenario} | {peak:.0f}/s ({best.load}) | {per_ms:.2f} | "
            f"{burst[0]:.0f}–{burst[1]:.0f}/s | {sustained[0]:.0f}–{sustained[1]:.0f}/s |"
        )
    return lines


def run(args: argparse.Namespace) -> Path:
    info = run_info(NAME, "all", args.tag, Path(__file__))
    texts = Texts(args.chars)
    scenarios = [s for s in args.scenarios.split(",") if s]
    unknown = sorted(set(scenarios) - set(SCENARIOS))
    if unknown:
        raise SystemExit(f"unknown scenarios: {', '.join(unknown)}")
    remote = bool(args.engine_url or args.gateway_url)
    if args.rates:
        loads = [f"r{r}" for r in args.rates.split(",") if r]
    else:
        loads = [f"c{c}" for c in args.concurrency.split(",") if c]
    steps: list[Step] = []
    stack = None
    cpus = None if args.server_cpus == "all" else [int(c) for c in args.server_cpus.split(",")]
    if remote:
        keys = [k for k in os.environ.get("UMAI_LOAD_API_KEYS", "").split(",") if k.strip()]
        if not keys:
            raise SystemExit("remote mode needs the test tenant's keys in UMAI_LOAD_API_KEYS")
        target = Target(
            args.engine_url,
            args.gateway_url,
            [k.strip() for k in keys],
            os.environ.get("UMAI_LOAD_PROVIDER_KEY", ""),
            args.application,
            {"evaluate-l0": args.bundle_l0, "evaluate-l1b": args.bundle_l1b},
            texts,
        )
        for scenario in scenarios:
            for load in loads:
                steps.append(_run_step(None, target, scenario, load, args))
                print(f"{scenario} {load}: {steps[-1].ok / args.duration_s:.0f}/s", flush=True)
        context = [
            f"- **Target:** {args.engine_url or '—'} (engine API), "
            f"{args.gateway_url or '—'} (gateway)"
        ]
    else:
        count = os.cpu_count() or 2
        # The generator and the stubs on the other fast cores (hybrid CPUs: performance cores)
        client = [c for c in performance_cpus() if cpus is None or c not in cpus]
        client = client or [c for c in range(count) if cpus is None or c not in cpus] or None
        work = Path(tempfile.mkdtemp(prefix="umai-load-"))
        # One key carries the whole load: its per-key rate limit is lifted (the limiter still
        # runs on every request, so its cost is measured).
        extra = {"UMAI_DE_RATE_LIMIT_RPS": "1000000", "UMAI_DE_RATE_LIMIT_BURST": "1000000"}
        with LocalStack(
            work,
            server_cpus=cpus,
            client_cpus=client if cpus is not None else None,
            stub_latency_ms=args.stub_latency_ms,
            extra_env=extra,
        ) as stack:
            stack.start()
            # The beta plan's arrangement: a dedicated tenant whose monthly quota is lifted.
            stack.create_tenant({"monthly_requests": 1_000_000_000})
            secret, _ = stack.mint_key("load", ["evaluate", "gateway"])
            time.sleep(6)  # one registry poll
            target = Target(
                stack.services["engine"].url,
                stack.services["gateway"].url,
                [secret],
                PROVIDER_KEY,
                APPLICATION,
                {"evaluate-l0": DEMO_BUNDLE, "evaluate-l1b": L1B_BUNDLE},
                texts,
            )
            for scenario in scenarios:
                for load in loads:
                    steps.append(_run_step(stack, target, scenario, load, args))
                    print(f"{scenario} {load}: {steps[-1].ok / args.duration_s:.0f}/s", flush=True)
            idle = {n: s.stats() for n, s in stack.services.items()}
            pinned = dict(stack.affinity)
        rss = {n: v[1] for n, v in idle.items() if v is not None}
        context = [
            "- **Stack:** `evals/stack.py`, `UMAI_DE_ENV=production`, one uvicorn process each "
            "(as on the VM), access logs on, records off, rate limiter on with a lifted limit, "
            "quota counted against a lifted plan override",
            f"- **CPUs:** services on logical CPUs {args.server_cpus} "
            f"({'pinned' if all(pinned.values()) and pinned else 'not pinned'}); load "
            f"generator and stubs on {client if cpus is not None else 'any'}; {count} logical "
            "CPUs on the host",
            f"- **Stubs:** answer after {args.stub_latency_ms:g} ms; L1b policies enforcing: "
            f"{', '.join(L1B_ENFORCED)} (one call on the request path, timeout "
            f"{L1B_TIMEOUT_MS} ms as on beta), the other seven in shadow (a second call after the "
            "answer)",
            "- **Resident memory after the run:** "
            + ", ".join(f"{n} {v / 2**20:.0f} MiB" for n, v in sorted(rss.items())),
        ]

    lines = [
        f"# Suite `{NAME}` — {info.tag}",
        "",
        *info.header_lines(),
        *context,
        f"- **Requests:** unique {args.chars}-character synthetic texts (no L0 cache hits); "
        + (
            f"open loop at {args.rates} requests/s"
            if args.rates
            else f"closed loop with {args.concurrency} workers"
        )
        + f", {args.warmup_s:g} s warm-up then {args.duration_s:g} s measured per step",
        "",
        "## Results",
        "",
        "Throughput counts 2xx answers per second. Degraded: 2xx evaluations decided by a "
        "failure policy (`degraded` or `errors` in the answer, e.g. an L1b call past its "
        "timeout in a saturated process). Server CPU: the scenario's process (engine or "
        "gateway) as a share of one CPU; CPU ms/request is that time per 2xx answer. Client CPU: "
        "the load generator, to show it was not the bottleneck. Host busy: the whole machine's "
        "CPU in use, this test included; other work on the host slows the run down.",
        "",
        *_table(steps),
        "",
        "Non-2xx answers, transport errors and degraded evaluations:",
        "",
        *_failures(steps),
        "",
    ]
    if not remote:
        lines += [
            "## What a B2s can carry (estimate)",
            "",
            "Scaled from the CPU time per request at the local peak. Assumptions: a B2s vCPU "
            "is 1.5–2.5× slower than this laptop's performance core (B-series hosts range from "
            "Haswell 2.4 GHz to Ice Lake; measure on the VM to narrow it); one uvicorn process "
            "uses at most one vCPU (Python's GIL); the policy service, Postgres, the console and "
            "Caddy (TLS) share the same two vCPUs and are not counted.",
            "",
            *_estimate(steps, scenarios),
            "",
        ]
    directory = report_dir(info)
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
