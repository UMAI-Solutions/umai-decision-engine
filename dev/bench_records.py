"""Records overhead on the request path (ADR-0042 Validation): the same evaluations with the no-op
recorder and with the database recorder (queue + batched writer), sequential and concurrent.
Synthetic inputs from the l0-pii-tr dev split; the repository's demo guardrail.

python dev/bench_records.py --database-url <app-role url> [--admin-url <owner url>] [--n 600]

Without --database-url it uses a temporary SQLite file. Writes evals/reports/<date>-records-
overhead/summary.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from umai_decision_engine.contracts.evaluation import EvaluationRequest
from umai_decision_engine.db.migrate import upgrade
from umai_decision_engine.db.repositories import TenantRepository
from umai_decision_engine.db.session import make_engine, sqlite_url
from umai_decision_engine.events.database import DatabaseRecordWriter
from umai_decision_engine.events.recorder import MemoryRecorder, NullRecorder, QueueRecorder
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.pipeline.orchestrator import Evaluator
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "evals" / "datasets" / "l0-pii-tr" / "v2.jsonl"
TENANT = "bench"


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _requests(n: int) -> list[EvaluationRequest]:
    texts = [json.loads(line)["text"] for line in DATASET.read_text(encoding="utf-8").splitlines()]
    texts = [t for t in texts if t][:200]
    return [
        EvaluationRequest.model_validate(
            {
                "contract_version": "0.12",
                "request_id": f"bench-{i}",
                "timestamp": datetime.now(UTC).isoformat(),
                "tenant_id": TENANT,
                "bundle": {"id": "gr-demo-data-protection"},
                "phase": "PRE_LLM",
                "content": {
                    "messages": [{"role": "user", "content": texts[i % len(texts)]}],
                    "focus": "LAST_USER_MESSAGE",
                },
            }
        )
        for i in range(n)
    ]


async def _run(evaluator: Evaluator, requests: list[EvaluationRequest], concurrency: int):
    latencies: list[float] = []
    semaphore = asyncio.Semaphore(concurrency)

    async def one(request: EvaluationRequest) -> None:
        async with semaphore:
            started = time.perf_counter()
            await evaluator.evaluate(request)
            latencies.append((time.perf_counter() - started) * 1000)

    started = time.perf_counter()
    await asyncio.gather(*(one(r) for r in requests))
    return latencies, time.perf_counter() - started


async def main(args: argparse.Namespace) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        url = args.database_url or sqlite_url(Path(tmp) / "bench.sqlite")
        upgrade(args.admin_url or url)
        engine = make_engine(url)
        await TenantRepository(engine).get_or_create(TENANT, "eu")
        store = BundleStore(
            DirectorySource(ROOT / "policies"),
            public_keys={},
            require_signed=False,
            default_tenant=TENANT,
        )
        store.reload(force=True)
        detector = EntityDetector(lambda _t: b"bench-key", threads=4)
        detector.warm_up()
        settings = Settings(l0_threads=4)
        requests = _requests(args.n)
        rows = []
        for concurrency in (1, 16):
            for label in ("none", "memory", "database"):
                if label == "none":
                    recorder, writer = NullRecorder(), None
                elif label == "memory":  # records built and kept: the cost without the writer
                    recorder, writer = MemoryRecorder(), None
                else:
                    writer = DatabaseRecordWriter(engine)
                    recorder = QueueRecorder(writer)
                    recorder.start()
                evaluator = Evaluator(store, detector, settings, recorder=recorder)
                await _run(evaluator, requests[:50], concurrency)  # warm-up (L0 cache off: new ids)
                latencies, seconds = await _run(evaluator, requests, concurrency)
                written = None
                if isinstance(recorder, QueueRecorder):
                    await recorder.aclose(30)
                    written = (recorder.written, dict(recorder.dropped))
                rows.append(
                    {
                        "mode": label,
                        "concurrency": concurrency,
                        "p50": statistics.median(latencies),
                        "p95": _quantile(latencies, 0.95),
                        "p99": _quantile(latencies, 0.99),
                        "per_s": len(latencies) / seconds,
                        "written": written,
                    }
                )
        detector.close()
        await engine.dispose()

    where = "PostgreSQL" if args.database_url else "SQLite"
    kind = "local Docker postgres:17-alpine" if args.database_url else "temporary file"
    directory = ROOT / "evals" / "reports" / f"{datetime.now(UTC):%Y-%m-%d}-records-overhead"
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "# records overhead",
        "",
        f"- **Date:** {datetime.now(UTC):%Y-%m-%d}",
        f"- **Store:** {where} ({kind})",
        f"- **Evaluations:** {args.n} per run, synthetic `l0-pii-tr` dev texts, guardrail "
        "`gr-demo-data-protection` (active version), PRE_LLM, 4 L0 threads",
        "- **Compared:** the no-op recorder against the database recorder (queue of ADR-0042, "
        "batched writer); latency of `Evaluator.evaluate`, so the write is off the measured path "
        "by design and any difference is the cost of building and queueing records",
        "",
        "| Recorder | Concurrency | p50 ms | p95 ms | p99 ms | Evaluations/s "
        "| Records written (dropped) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        written = "—" if r["written"] is None else f"{r['written'][0]} ({r['written'][1] or 0})"
        lines.append(
            f"| {r['mode']} | {r['concurrency']} | {r['p50']:.2f} | {r['p95']:.2f} | "
            f"{r['p99']:.2f} | {r['per_s']:.0f} | {written} |"
        )
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    return directory


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url")
    parser.add_argument("--admin-url")
    parser.add_argument("--n", type=int, default=600)
    asyncio.run(main(parser.parse_args()))
