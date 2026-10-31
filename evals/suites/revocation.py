"""Suite `revocation`: how long a revoked key keeps working (launch plan S0-2: at most 10 s;
ADR-0023 §6). Also how long a new key takes to work.

On a local stack of real processes (``evals/stack.py``: production settings, signed snapshots,
the default registry poll interval): mint a key in the policy service, probe the engine API
(``POST /v1/evaluate``) and the gateway (``GET /v1/models``) every 100 ms until both accept it,
revoke it, and probe both until they answer 401. A latency runs from the moment the request was
sent to the policy service to the end of the first probe with the new answer: an upper bound,
the switch happened after the previous probe. A random pause between rounds spreads the
revocations over the poll cycle. ``--background-load N`` keeps N workers calling the engine and
the gateway with another key during the run, to see whether a busy process reloads later.
Synthetic keys and text; no network beyond 127.0.0.1.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import tempfile
import time
from pathlib import Path

import httpx

from evals.common import report_dir, run_info
from evals.stack import APPLICATION, DEMO_BUNDLE, PROVIDER_KEY, LocalStack
from umai_decision_engine.settings import Settings

NAME = "revocation"
BUDGET_S = 10.0  # launch plan S0-2
PROBE_INTERVAL_S = 0.1  # 10 calls/s per service: under the default 20/s per key


def default_poll_s() -> float:
    """The key-registry poll interval a deployment gets unless it sets one."""

    return Settings(_env_file=None).key_registry_poll_interval_s  # type: ignore[call-arg]


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--rounds", type=int, default=20, help="revocation: keys revoked")
    parser.add_argument(
        "--background-load", type=int, default=0, help="revocation: concurrent workers on the side"
    )


def evaluate_body(text: str, request_id: str, bundle: str = DEMO_BUNDLE) -> dict:
    return {
        "contract_version": "0.12",
        "request_id": request_id,
        "timestamp": "2026-10-31T10:00:00Z",
        "bundle": {"id": bundle},
        "phase": "PRE_LLM",
        "content": {"messages": [{"role": "user", "content": text}], "focus": "LAST_USER_MESSAGE"},
    }


class Probe:
    def __init__(self, stack: LocalStack, client: httpx.AsyncClient) -> None:
        self.engine = stack.services["engine"].url
        self.gateway = stack.services["gateway"].url
        self.client = client
        self.calls = 0

    async def status(self, service: str, secret: str) -> int:
        self.calls += 1
        headers = {"Authorization": f"Bearer {secret}"}
        if service == "engine":
            body = evaluate_body("Haftalık toplantı notlarını özetler misin?", f"rv-{self.calls}")
            url = f"{self.engine}/v1/evaluate"
            response = await self.client.post(url, json=body, headers=headers)
        else:
            headers |= {"X-Umai-Application": APPLICATION, "X-Umai-Provider-Key": PROVIDER_KEY}
            response = await self.client.get(f"{self.gateway}/v1/models", headers=headers)
        return response.status_code

    async def until(self, service: str, secret: str, refused: bool, since: float) -> float:
        """Seconds from ``since`` to the end of the first probe that is refused (401) or, with
        ``refused=False``, answered (200). Anything else (a 429) decides nothing."""

        while True:
            status = await self.status(service, secret)
            if status == (401 if refused else 200):
                return time.perf_counter() - since
            if time.perf_counter() - since > 60:
                raise RuntimeError(f"{service}: no change after 60 s (last HTTP {status})")
            await asyncio.sleep(PROBE_INTERVAL_S)


async def _background(stack: LocalStack, secret: str, workers: int, stop: asyncio.Event) -> int:
    headers = {"Authorization": f"Bearer {secret}"}
    gateway = {**headers, "X-Umai-Application": APPLICATION, "X-Umai-Provider-Key": PROVIDER_KEY}
    done = 0

    async def worker(n: int, client: httpx.AsyncClient) -> None:
        nonlocal done
        while not stop.is_set():
            text = f"Arka plan isteği {n}-{done}: toplantı notlarını ve bütçe planını özetle."
            if n % 2:
                await client.post(
                    f"{stack.services['engine'].url}/v1/evaluate",
                    json=evaluate_body(text, f"bg-{n}-{done}"),
                    headers=headers,
                )
            else:
                await client.post(
                    f"{stack.services['gateway'].url}/v1/chat/completions",
                    json={"model": "stub-model", "messages": [{"role": "user", "content": text}]},
                    headers=gateway,
                )
            done += 1

    limits = httpx.Limits(max_connections=workers, max_keepalive_connections=workers)
    async with httpx.AsyncClient(timeout=30, limits=limits) as client:
        await asyncio.gather(*(worker(n, client) for n in range(workers)))
    return done


async def _measure(stack: LocalStack, rounds: int, background: int) -> tuple[dict, int, float]:
    results: dict[str, dict[str, list[float]]] = {
        s: {"revoked": [], "activated": []} for s in ("engine", "gateway")
    }
    answered: list[float] = []  # the policy service's own answer to the revocation
    stop = asyncio.Event()
    load = None
    started = time.perf_counter()
    if background:
        secret, _ = await asyncio.to_thread(stack.mint_key, "background", ["evaluate", "gateway"])
        await asyncio.sleep(default_poll_s() + 1)
        load = asyncio.create_task(_background(stack, secret, background, stop))
    async with httpx.AsyncClient(timeout=30) as client:
        probe = Probe(stack, client)
        for n in range(rounds):
            await asyncio.sleep(random.uniform(0, 5))  # a new phase of the poll cycle
            sent = time.perf_counter()
            secret, key_id = await asyncio.to_thread(
                stack.mint_key, f"revocation-{n}", ["evaluate", "gateway"]
            )
            accepted = await asyncio.gather(
                *(probe.until(s, secret, refused=False, since=sent) for s in results)
            )
            await asyncio.sleep(random.uniform(0, 5))
            sent = time.perf_counter()
            await asyncio.to_thread(stack.revoke_key, key_id)
            answered.append(time.perf_counter() - sent)
            refused = await asyncio.gather(
                *(probe.until(s, secret, refused=True, since=sent) for s in results)
            )
            for service, a, r in zip(results, accepted, refused, strict=True):
                results[service]["activated"].append(a)
                results[service]["revoked"].append(r)
            print(f"round {n + 1}/{rounds}: refused after {max(refused):.2f} s", flush=True)
    stop.set()
    background_calls = await load if load is not None else 0
    rate = background_calls / (time.perf_counter() - started) if background else 0.0
    results["policy"] = {"answered": answered}
    return results, background_calls, rate


def _row(label: str, samples: list[float]) -> str:
    ordered = sorted(samples)
    p95 = ordered[0]
    if len(ordered) > 1:
        p95 = statistics.quantiles(ordered, n=20, method="inclusive")[18]
    return (
        f"| {label} | {len(samples)} | {statistics.median(ordered):.2f} s | {p95:.2f} s | "
        f"{ordered[-1]:.2f} s |"
    )


def run(args: argparse.Namespace) -> Path:
    info = run_info(NAME, "all", args.tag, Path(__file__))
    rounds, background = args.rounds, args.background_load
    poll = default_poll_s()
    # Probes of a key that is not loaded yet are bad credentials: at 10 a second the failed-
    # credential throttle (ADR-0046 D3) would answer 429 for the source after two seconds and
    # hide the switch. It is not what is measured here, so its limit is lifted.
    extra = {"UMAI_DE_AUTH_FAILURE_LIMIT": "100000"}
    if background:  # one key carries the whole background load: lift its rate limit
        extra |= {"UMAI_DE_RATE_LIMIT_RPS": "100000", "UMAI_DE_RATE_LIMIT_BURST": "100000"}
    work = Path(tempfile.mkdtemp(prefix="umai-revocation-"))
    with LocalStack(work, extra_env=extra) as stack:
        stack.start()
        stack.create_tenant({"monthly_requests": 100_000_000})
        results, calls, rate = asyncio.run(_measure(stack, rounds, background))

    worst = max(max(results[s]["revoked"]) for s in ("engine", "gateway"))
    verdict = "PASS" if worst <= BUDGET_S else "FAIL"
    rows = []
    for service, label in (("engine", "engine API"), ("gateway", "gateway")):
        rows.append(_row(f"{label}: revoked key refused (401)", results[service]["revoked"]))
    for service, label in (("engine", "engine API"), ("gateway", "gateway")):
        rows.append(_row(f"{label}: new key accepted", results[service]["activated"]))
    rows.append(_row("policy service: revocation answered", results["policy"]["answered"]))
    load_line = (
        f"- **Background load:** {background} workers on the engine and the gateway with another "
        f"key, {calls} calls ({rate:.0f}/s) during the run"
        if background
        else "- **Background load:** none (idle stack)"
    )
    lines = [
        f"# Suite `{NAME}` — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Stack:** policy service, engine API and gateway as separate uvicorn processes "
        f"(`evals/stack.py`), `UMAI_DE_ENV=production`, signed key snapshots in a shared "
        f"directory, registry poll every {poll:g} s (the default; the VM does not override it)",
        f"- **Rounds:** {rounds}; probes every {PROBE_INTERVAL_S * 1000:.0f} ms per service "
        "(accepted: 200, refused: 401); random pauses of 0–5 s spread the changes over the poll "
        "cycle; the failed-credential throttle's limit is lifted, since probes of a key not yet "
        "loaded would trip it",
        load_line,
        "",
        f"## Verdict: {verdict} (slowest refusal {worst:.2f} s; budget {BUDGET_S:g} s)",
        "",
        "Latency: from sending the change to the policy service to the end of the first probe "
        "with the new answer (an upper bound; probes add up to ~0.1 s).",
        "",
        "| What | Rounds | p50 | p95 | max |",
        "|---|---|---|---|---|",
        *rows,
        "",
        f"**Reading.** The policy service writes the tenant's signed snapshot before it answers; "
        f"each process reads the directory every {poll:g} s, so a change takes effect after at "
        f"most one poll interval plus the reload. With one shared directory (the single VM) "
        "nothing else is in the path. A stack whose engines pull snapshots over HTTP (ADR-0025 "
        "§8) adds its pull interval and must be measured again.",
        "",
    ]
    directory = report_dir(info)
    (directory / "summary.md").write_text("\n".join(lines), encoding="utf-8")
    return directory
