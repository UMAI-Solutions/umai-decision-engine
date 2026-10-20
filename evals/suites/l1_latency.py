# ruff: noqa: E501  (report table rows)
"""Suite `l1-latency` (live): one call with every question vs one call per policy (ADR-0040).

For each case of the `pi-tr` set (synthetic Turkish prompts, attacks and ordinary work) the
text goes through L0 and is redacted exactly as the pipeline does, then the bundle's L1b
policies are asked twice, in alternating order: ``all`` (one Decisions call with every
question) and ``per_policy`` (one call per policy, in parallel). Reported per mode: latency
p50 / p95 / p99 and the share above the L1b budget, tokens, errors and refusals; and how often
both modes give the same answer per question.

Needs ``UMAI_EVAL_OPENAI_API_KEY`` (never written to reports or logs). Record where it ran:
latency depends on the region (``--region-note``).
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import random
import statistics
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import yaml

from evals.common import ROOT, load_cases, report_dir, run_info, write_results
from umai_decision_engine.contracts.bundle import PolicyBundle
from umai_decision_engine.contracts.evaluation import EvaluationRequest
from umai_decision_engine.contracts.questions import Answer
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.l0.target import extract_target_parts
from umai_decision_engine.l1.decision_models.base import DecisionModelError
from umai_decision_engine.l1.decision_models.openai_decisions import OpenAIDecisions
from umai_decision_engine.l1.questions import decision_request, excerpt, facts
from umai_decision_engine.pipeline.l0_policies import phase_config, type_rules
from umai_decision_engine.pipeline.l1_input import redacted_parts

NAME = "l1-latency"
KEY_ENV = "UMAI_EVAL_OPENAI_API_KEY"
DATASET = ROOT / "evals" / "datasets" / "pi-tr" / "v1.jsonl"
DEFAULT_BUNDLE = ROOT / "policies" / "gr-demo-data-protection" / "5" / "bundle.yaml"


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def _winner(answer: Answer | None) -> Any:
    if answer is None or answer.refused:
        return "refused"
    if answer.value is not None:
        return answer.value
    if answer.level is not None:
        return answer.level
    return None if answer.probability is None else answer.probability >= 0.5


async def _measure(args: argparse.Namespace, key: str) -> tuple[list[dict], dict[str, Any]]:
    bundle = PolicyBundle.model_validate(
        yaml.safe_load(Path(args.l1_bundle or DEFAULT_BUNDLE).read_text(encoding="utf-8"))
    )
    cases = load_cases(DATASET, args.split)
    random.Random(7).shuffle(cases)
    cases = cases[: args.limit]
    detector = EntityDetector(lambda _tenant: b"eval-key", threads=1)
    detector.warm_up()
    rules = type_rules(bundle.l0)
    timeout_s = args.timeout_ms / 1000
    rows: list[dict] = []
    async with httpx.AsyncClient(follow_redirects=False) as client:
        model = OpenAIDecisions(
            "openai", client, base_url=args.base_url, model="gpt-6-luna", api_key=key
        )
        for index, case in enumerate(cases):
            phase = case.get("phase") or "PRE_LLM"
            request = EvaluationRequest.model_validate(
                {
                    "contract_version": "0.12",
                    "request_id": f"eval-{case['case_id']}",
                    "timestamp": "2026-10-20T00:00:00Z",
                    "tenant_id": "eval",
                    "bundle": {"id": bundle.id},
                    "phase": phase,
                    "content": {
                        "messages": [{"role": "user", "content": case["text"]}],
                        "focus": "LAST_USER_MESSAGE",
                    },
                }
            )
            policies = [p for p in bundle.l1b.policies if request.phase in p.phases]
            if not policies:
                continue
            parts = extract_target_parts(request.content, "LAST_MESSAGE")
            l0 = await detector.adetect_segments(
                [t for _, t in parts],
                tenant_id="eval",
                config=phase_config(bundle.l0, rules, request.phase),
                cache=False,
            )
            judged = excerpt(redacted_parts(parts, l0.entities), bundle.l1b.max_input_chars)
            known = facts(request, l0.summary)
            modes = ["all", "per_policy"] if index % 2 == 0 else ["per_policy", "all"]
            row: dict[str, Any] = {"case_id": case["case_id"], "label": case.get("label")}
            for mode in modes:
                started = time.perf_counter()
                try:
                    if mode == "all":
                        results = [
                            await model.decide(
                                decision_request(request.request_id, policies, judged, known),
                                timeout_s=timeout_s,
                            )
                        ]
                    else:
                        results = await asyncio.gather(
                            *(
                                model.decide(
                                    decision_request(request.request_id, [p], judged, known),
                                    timeout_s=timeout_s,
                                )
                                for p in policies
                            )
                        )
                except DecisionModelError as exc:
                    row[mode] = {"error": exc.kind, "ms": (time.perf_counter() - started) * 1000}
                    continue
                answers: dict[str, Answer] = {}
                usage: Counter[str] = Counter()
                for result in results:
                    answers.update(result.answers)
                    usage.update({k: v for k, v in result.usage.items() if isinstance(v, int)})
                row[mode] = {
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                    "answers": {qid: _winner(a) for qid, a in answers.items()},
                    "refusals": sum(1 for a in answers.values() if a.refused),
                    "input_tokens": usage.get("input_tokens", 0),
                    "cached_tokens": usage.get("cached_tokens", 0),
                    "injection_p": (
                        (answers.get("prompt-attack.injection").probabilities or {}).get(
                            "injection_detected"
                        )
                        if answers.get("prompt-attack.injection")
                        else None
                    ),
                }
            rows.append(row)
    detector.close()
    meta = {
        "policies": len(bundle.l1b.policies),
        "questions": sum(len(p.questions) for p in bundle.l1b.policies),
        "bundle": f"{bundle.id}@{bundle.version}",
    }
    return rows, meta


def run(args: argparse.Namespace) -> Path:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise SystemExit(f"{NAME} needs {KEY_ENV}")
    info = run_info(NAME, args.split, args.tag, DATASET)
    rows, meta = asyncio.run(_measure(args, key))
    directory = report_dir(info)
    write_results(directory, rows)
    budget = args.budget_ms
    lines = [
        f"# {NAME} — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Provider:** OpenAI Decisions `gpt-6-luna` at `{args.base_url}`; "
        f"bundle `{meta['bundle']}` ({meta['policies']} policies, {meta['questions']} questions)",
        f"- **Where it ran:** {args.region_note or 'not recorded'}",
        f"- **Cases:** {len(rows)}; client timeout {args.timeout_ms} ms (to see the whole "
        f"distribution); L1b budget {budget} ms",
        "",
        "| Mode | Calls per evaluation | p50 ms | p95 ms | p99 ms | Over budget | Errors | Refused answers | Input tokens / eval | Cached |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for mode, calls in (("all", 1), ("per_policy", meta["policies"])):
        ok = [r[mode] for r in rows if mode in r and "error" not in r[mode]]
        times = [m["ms"] for m in ok]
        errors = Counter(r[mode]["error"] for r in rows if mode in r and "error" in r[mode])
        over = sum(1 for t in times if t > budget)
        tokens = statistics.mean(m["input_tokens"] for m in ok) if ok else 0
        cached = statistics.mean(m["cached_tokens"] for m in ok) if ok else 0
        lines.append(
            f"| `{mode}` | {calls} | {_fmt(_quantile(times, 0.5))} | {_fmt(_quantile(times, 0.95))} "
            f"| {_fmt(_quantile(times, 0.99))} | {over}/{len(times)} | "
            f"{sum(errors.values())} {dict(errors) or ''} | {sum(m['refusals'] for m in ok)} | "
            f"{tokens:.0f} | {cached:.0f} |"
        )
    both = [
        r for r in rows if "answers" in r.get("all", {}) and "answers" in r.get("per_policy", {})
    ]
    same = total = 0
    for r in both:
        for qid, value in r["all"]["answers"].items():
            total += 1
            same += value == r["per_policy"]["answers"].get(qid)
    lines += [
        "",
        f"**Same answer in both modes:** {same}/{total} questions "
        f"({(same / total * 100) if total else 0:.1f} %) over {len(both)} cases.",
        "",
        "Prompt-attack probabilities and labels per case are in `results.jsonl` (not committed); "
        "thresholds are set in step 3 on the dev split, not here.",
    ]
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return directory


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f}"


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", default="https://api.openai.com/v1", help="l1-latency")
    parser.add_argument("--limit", type=int, default=40, help="l1-latency: cases")
    parser.add_argument("--timeout-ms", type=int, default=10_000, help="l1-latency: client timeout")
    parser.add_argument("--budget-ms", type=int, default=600, help="l1-latency: L1b budget")
    parser.add_argument("--l1-bundle", help="l1-latency: bundle.yaml with l1b policies")
    parser.add_argument("--region-note", help="l1-latency: where this ran (e.g. Azure West Europe)")
