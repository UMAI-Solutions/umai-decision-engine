# ruff: noqa: E501  (report table rows)
"""Suite `l1-cold-start` (live): the first L1b calls of a new process (ADR-0040 amendment
2026-10-26).

Each run starts fresh worker processes, alternating two variants:

- ``before``: the provider client as before the amendment: no warm-up, idle connections closed
  after 5 s (httpx's default).
- ``after``: as the engine and gateway start now: ``DecisionModels.warm_up`` at startup, idle
  connections kept for ``UMAI_DE_DECISION_KEEPALIVE_S``.

A worker imports the engine and gateway apps (the imports a real process has done), builds the
providers from settings, then makes three decision calls back to back, waits ``--gap-s`` and
makes a fourth. Every call has the bundle's policies and a different synthetic sentence without
entities (nothing for L0 to redact). Per call it records the latency the adapter reports,
whether a new connection was opened, and OpenAI's own ``openai-processing-ms`` header: total
minus processing is what the client and the network add, which is what a warm-up can remove.

Needs ``UMAI_EVAL_OPENAI_API_KEY`` (passed to workers in their environment, never on the command
line, in reports or logs). Record where it ran (``--region-note``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from evals.common import ROOT, report_dir, run_info, write_results
from evals.suites.l1_latency import DEFAULT_BUNDLE, KEY_ENV, _fmt, _quantile

NAME = "l1-cold-start"
VARIANTS = ("before", "after")
TEXTS = [
    "Merhaba, yarınki ekip toplantısı için gündem maddelerini kısa bir liste halinde özetler misin?",
    "Bu e-postayı daha kibar bir dille yeniden yazar mısın? Konu: teslim tarihinin ertelenmesi.",
    "Python'da bir listeyi tersine çevirmenin en kısa yolu nedir? Kısa bir örnek ver.",
    "Haftalık satış raporu için üç başlık önerir misin? Bölge ve ürün bazında olsun.",
]


# ---------------------------------------------------------------------- worker (one process)


async def _worker(args: argparse.Namespace) -> list[dict[str, Any]]:
    import httpx
    import yaml

    # A real engine or gateway process has made these imports before its first decision.
    import umai_decision_engine.api.app
    import umai_decision_engine.gateway.app  # noqa: F401
    from umai_decision_engine.contracts.bundle import PolicyBundle
    from umai_decision_engine.contracts.questions import ExcerptMessage
    from umai_decision_engine.l1.decision_models.base import DecisionModelError
    from umai_decision_engine.l1.decision_models.registry import DecisionModels
    from umai_decision_engine.l1.questions import decision_request
    from umai_decision_engine.settings import Settings

    bundle = PolicyBundle.model_validate(
        yaml.safe_load(Path(args.l1_bundle or DEFAULT_BUNDLE).read_text(encoding="utf-8"))
    )
    policies = [p for p in bundle.l1b.policies if "PRE_LLM" in p.phases]
    known = {"phase": "PRE_LLM", "destination": {"class": "unknown"}, "subject": {}, "entities": []}
    after = args.worker == "after"
    settings = Settings(
        _env_file=None,
        decision_providers={
            "openai": {
                "adapter": "openai_decisions",
                "base_url": args.base_url,
                "model": "gpt-6-luna",
            }
        },
        decision_provider_keys={"openai": os.environ[KEY_ENV].strip()},
        decision_keepalive_s=args.keepalive_s if after else 5.0,
    )
    models = DecisionModels.from_settings(settings)
    rows: list[dict[str, Any]] = []
    if after:
        started = time.perf_counter()
        outcome = (await models.warm_up(args.warm_up_ms / 1000))["openai"]
        rows.append({"call": "warm_up", "outcome": outcome, "ms": _ms(started)})

    # Observation only: phase events and the provider's processing time, never bodies.
    seen: dict[str, Any] = {}

    async def trace(name: str, info: dict) -> None:
        if name == "connection.connect_tcp.started":
            seen["new_connection"] = True

    async def on_request(request: httpx.Request) -> None:
        seen.clear()
        request.extensions["trace"] = trace

    async def on_response(response: httpx.Response) -> None:
        seen["processing_ms"] = response.headers.get("openai-processing-ms")

    client = models._client
    assert client is not None
    client.event_hooks = {"request": [on_request], "response": [on_response]}
    model = models.get("openai")
    assert model is not None
    for index, text in enumerate(TEXTS, start=1):
        if index == len(TEXTS):
            await asyncio.sleep(args.gap_s)
        request = decision_request(
            f"cold-{index}", policies, [ExcerptMessage(role="user", content=text)], known
        )
        call_started = time.perf_counter()
        row: dict[str, Any] = {"call": index}
        try:
            result = await model.decide(request, timeout_s=args.timeout_ms / 1000)
            row["ms"] = result.latency_ms
        except DecisionModelError as exc:
            row.update(error=exc.kind, ms=_ms(call_started))
        processing = seen.get("processing_ms")
        row["processing_ms"] = int(processing) if processing and processing.isdigit() else None
        row["new_connection"] = bool(seen.get("new_connection"))
        rows.append(row)
    await models.aclose()
    return rows


def _ms(since: float) -> float:
    return round((time.perf_counter() - since) * 1000, 1)


# ---------------------------------------------------------------------- parent


def _spawn(args: argparse.Namespace, variant: str, key: str) -> list[dict[str, Any]]:
    command = [
        sys.executable,
        "-m",
        "evals.suites.l1_cold_start",
        "--worker",
        variant,
        "--base-url",
        args.base_url,
        "--gap-s",
        str(args.gap_s),
        "--keepalive-s",
        str(args.keepalive_s),
        "--timeout-ms",
        str(args.timeout_ms),
        "--warm-up-ms",
        str(args.warm_up_ms),
    ]
    if args.l1_bundle:
        command += ["--l1-bundle", args.l1_bundle]
    done = subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, KEY_ENV: key},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    if done.returncode != 0:
        raise SystemExit(f"{NAME}: worker failed ({variant}):\n{done.stderr[-2000:]}")
    return [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]


def run(args: argparse.Namespace) -> Path:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise SystemExit(f"{NAME} needs {KEY_ENV}")
    bundle = Path(args.l1_bundle or DEFAULT_BUNDLE)
    info = run_info(NAME, "all", args.tag, bundle)
    rows: list[dict[str, Any]] = []
    for index in range(args.processes):
        for variant in VARIANTS if index % 2 == 0 else reversed(VARIANTS):
            for row in _spawn(args, variant, key):
                rows.append({"process": index, "variant": variant, **row})
            time.sleep(args.pause_s)
    directory = report_dir(info)
    write_results(directory, rows)
    lines = [
        f"# {NAME} — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Provider:** OpenAI Decisions `gpt-6-luna` at `{args.base_url}`; bundle `{bundle.as_posix()}`",
        f"- **Where it ran:** {args.region_note or 'not recorded'}",
        f"- **Processes:** {args.processes} per variant, alternating; calls 1–3 back to back, "
        f"call 4 after {args.gap_s:g} s idle; client timeout {args.timeout_ms} ms; `after` keeps "
        f"idle connections {args.keepalive_s:g} s, `before` 5 s",
        "",
        "| Variant | Call | Calls | p50 ms | max ms | New connections | Client + network p50 / max ms | OpenAI processing p50 / max ms | Errors |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for variant in VARIANTS:
        for call, label in (
            (1, "1 (first)"),
            (2, "2"),
            (3, "3"),
            (4, f"4 (after {args.gap_s:g} s)"),
        ):
            got = [r for r in rows if r["variant"] == variant and r["call"] == call]
            ok = [r for r in got if "error" not in r]
            times = [r["ms"] for r in ok]
            client = [r["ms"] - r["processing_ms"] for r in ok if r["processing_ms"] is not None]
            processing = [r["processing_ms"] for r in ok if r["processing_ms"] is not None]
            errors = [r["error"] for r in got if "error" in r]
            lines.append(
                f"| `{variant}` | {label} | {len(got)} | {_fmt(_quantile(times, 0.5))} | "
                f"{_fmt(max(times) if times else None)} | "
                f"{sum(r['new_connection'] for r in got)}/{len(got)} | "
                f"{_fmt(_quantile(client, 0.5))} / {_fmt(max(client) if client else None)} | "
                f"{_fmt(_quantile(processing, 0.5))} / {_fmt(max(processing) if processing else None)} | "
                f"{len(errors)} {sorted(set(errors)) or ''} |"
            )
    warm = [r for r in rows if r["call"] == "warm_up"]
    if warm:
        outcomes = sorted({r["outcome"] for r in warm})
        lines += [
            "",
            f"**Warm-up** (`after`, at startup): outcomes {outcomes}, "
            f"p50 {_fmt(statistics.median(r['ms'] for r in warm))} ms, "
            f"max {_fmt(max(r['ms'] for r in warm))} ms.",
        ]
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--processes", type=int, default=6, help="l1-cold-start: per variant")
    parser.add_argument(
        "--gap-s", type=float, default=8.0, help="l1-cold-start: idle before call 4"
    )
    parser.add_argument(
        "--pause-s", type=float, default=2.0, help="l1-cold-start: between processes"
    )
    parser.add_argument("--keepalive-s", type=float, default=120.0, help="l1-cold-start: `after`")
    parser.add_argument("--warm-up-ms", type=int, default=2000, help="l1-cold-start: `after`")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m evals.suites.l1_cold_start")
    parser.add_argument("--worker", choices=VARIANTS, required=True)
    parser.add_argument("--base-url", default="https://api.openai.com/v1")
    parser.add_argument("--timeout-ms", type=int, default=10_000)
    parser.add_argument("--l1-bundle")
    add_arguments(parser)
    for result in asyncio.run(_worker(parser.parse_args())):
        print(json.dumps(result), flush=True)
