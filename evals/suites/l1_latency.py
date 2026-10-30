# ruff: noqa: E501  (report table rows)
"""Suite `l1-latency` (live): how to ask a bundle's L1b policies, per call shape (ADR-0040).

For each case of the `pi-tr` set (synthetic Turkish prompts, attacks and ordinary work) the
text goes through L0 and is redacted exactly as the pipeline does, then the bundle's L1b
policies are asked once per mode, in alternating order (``--modes``, default ``all,per_policy``):

- ``all``: one Decisions call with every applicable question (the request path before the
  2026-10-30 amendment, when shadow policies rode along with enforcing ones);
- ``per_policy``: one call per policy, in parallel;
- ``split``: the enforcing policies in one timed call, then the shadow policies in their own
  call in the background, as the L1b stage does since the amendment. The background call is
  not awaited, so it may still be running when the next ``split`` call starts, as under real
  traffic; the other modes wait for it, so they measure the path they stand for.

Reported per mode: latency p50 / p95 / p99 / max and the share above the L1b budget, tokens,
errors and refusals; for ``split`` also its background call; and how often each mode gives the
same answer per question as ``all`` (enforcing questions on their own).

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
from umai_decision_engine.contracts.questions import Answer, DecisionResult
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
MODES = ("all", "per_policy", "split")


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


def _summary(results: list[DecisionResult], started: float) -> dict[str, Any]:
    answers: dict[str, Answer] = {}
    usage: Counter[str] = Counter()
    for result in results:
        answers.update(result.answers)
        usage.update({k: v for k, v in result.usage.items() if isinstance(v, int)})
    attack = answers.get("prompt-attack.injection")
    return {
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "answers": {qid: _winner(a) for qid, a in answers.items()},
        "refusals": sum(1 for a in answers.values() if a.refused),
        "input_tokens": usage.get("input_tokens", 0),
        "cached_tokens": usage.get("cached_tokens", 0),
        "injection_p": (attack.probabilities or {}).get("injection_detected") if attack else None,
    }


async def _timed(calls: list) -> tuple[list[DecisionResult] | None, str | None]:
    try:
        return list(await asyncio.gather(*calls)), None
    except DecisionModelError as exc:
        return None, exc.kind


async def _measure(args: argparse.Namespace, key: str) -> tuple[list[dict], dict[str, Any]]:
    bundle = PolicyBundle.model_validate(
        yaml.safe_load(Path(args.l1_bundle or DEFAULT_BUNDLE).read_text(encoding="utf-8"))
    )
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    unknown = set(modes) - set(MODES)
    if unknown or not modes:
        raise SystemExit(f"{NAME}: --modes takes {', '.join(MODES)}; got {args.modes!r}")
    cases = load_cases(DATASET, args.split)
    random.Random(7).shuffle(cases)
    cases = cases[: args.limit]
    detector = EntityDetector(lambda _tenant: b"eval-key", threads=1)
    detector.warm_up()
    rules = type_rules(bundle.l0)
    timeout_s = args.timeout_ms / 1000
    rows: list[dict] = []
    background: set[asyncio.Task] = set()
    async with httpx.AsyncClient(follow_redirects=False) as client:
        model = OpenAIDecisions(
            "openai", client, base_url=args.base_url, model="gpt-6-luna", api_key=key
        )

        def ask(request_id: str, policies, judged, known):
            return model.decide(
                decision_request(request_id, policies, judged, known), timeout_s=timeout_s
            )

        async def shadow_call(row: dict[str, Any], request_id: str, policies, judged, known):
            started = time.perf_counter()
            results, error = await _timed([ask(request_id, policies, judged, known)])
            row["split"]["shadow"] = (
                _summary(results, started)
                if results is not None
                else {"error": error, "ms": (time.perf_counter() - started) * 1000}
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
            enforce = [p for p in policies if p.enforcement == "enforce"]
            shadow = [p for p in policies if p.enforcement == "shadow"]
            parts = extract_target_parts(request.content, "LAST_MESSAGE")
            l0 = await detector.adetect_segments(
                [t for _, t in parts],
                tenant_id="eval",
                config=phase_config(bundle.l0, rules, request.phase),
                cache=False,
            )
            judged = excerpt(redacted_parts(parts, l0.entities), bundle.l1b.max_input_chars)
            known = facts(request, l0.summary)
            row: dict[str, Any] = {"case_id": case["case_id"], "label": case.get("label")}
            rid = request.request_id
            for mode in modes if index % 2 == 0 else list(reversed(modes)):
                if mode != "split" and background:  # these modes have no background call
                    await asyncio.gather(*list(background))
                if mode == "split" and not enforce:
                    continue
                overlap = bool(background)
                started = time.perf_counter()
                if mode == "all":
                    calls = [ask(rid, policies, judged, known)]
                elif mode == "per_policy":
                    calls = [ask(rid, [p], judged, known) for p in policies]
                else:
                    calls = [ask(rid, enforce, judged, known)]
                results, error = await _timed(calls)
                row[mode] = (
                    _summary(results, started)
                    if results is not None
                    else {"error": error, "ms": (time.perf_counter() - started) * 1000}
                )
                if mode == "split":
                    row[mode]["overlap"] = overlap
                    if shadow:
                        task = asyncio.create_task(shadow_call(row, rid, shadow, judged, known))
                        background.add(task)
                        task.add_done_callback(background.discard)
            rows.append(row)
        if background:
            await asyncio.gather(*list(background))
    detector.close()
    meta = {
        "policies": len(bundle.l1b.policies),
        "enforcing": [p.id for p in bundle.l1b.policies if p.enforcement == "enforce"],
        "questions": sum(len(p.questions) for p in bundle.l1b.policies),
        "bundle": f"{bundle.id}@{bundle.version}",
        "modes": modes,
    }
    return rows, meta


def _latency_cells(times: list[float]) -> str:
    return " | ".join(
        _fmt(v)
        for v in (
            _quantile(times, 0.5),
            _quantile(times, 0.95),
            _quantile(times, 0.99),
            max(times) if times else None,
        )
    )


def run(args: argparse.Namespace) -> Path:
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise SystemExit(f"{NAME} needs {KEY_ENV}")
    info = run_info(NAME, args.split, args.tag, DATASET)
    rows, meta = asyncio.run(_measure(args, key))
    directory = report_dir(info)
    write_results(directory, rows)
    budget = args.budget_ms
    enforcing = meta["enforcing"]
    lines = [
        f"# {NAME} — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Provider:** OpenAI Decisions `gpt-6-luna` at `{args.base_url}`; "
        f"bundle `{meta['bundle']}` ({meta['policies']} policies, {meta['questions']} questions; "
        f"enforcing: {', '.join(f'`{p}`' for p in enforcing) or 'none'})",
        f"- **Where it ran:** {args.region_note or 'not recorded'}",
        f"- **Cases:** {len(rows)}; modes {', '.join(f'`{m}`' for m in meta['modes'])} in "
        f"alternating order; client timeout {args.timeout_ms} ms (to see the whole "
        f"distribution); L1b budget {budget} ms",
        "",
        "| Mode | Calls per evaluation | p50 ms | p95 ms | p99 ms | max ms | Over budget | Errors | Refused answers | Input tokens / eval | Cached |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    calls_of = {"all": "1", "per_policy": str(meta["policies"]), "split": "1 (+1 after)"}
    for mode in meta["modes"]:
        ok = [r[mode] for r in rows if mode in r and "error" not in r[mode]]
        times = [m["ms"] for m in ok]
        errors = Counter(r[mode]["error"] for r in rows if mode in r and "error" in r[mode])
        over = sum(1 for t in times if t > budget)
        tokens = statistics.mean(m["input_tokens"] for m in ok) if ok else 0
        cached = statistics.mean(m["cached_tokens"] for m in ok) if ok else 0
        lines.append(
            f"| `{mode}` | {calls_of[mode]} | {_latency_cells(times)} | {over}/{len(times)} | "
            f"{sum(errors.values())} {dict(errors) or ''} | {sum(m['refusals'] for m in ok)} | "
            f"{tokens:.0f} | {cached:.0f} |"
        )
    if "split" in meta["modes"]:
        split = [r["split"] for r in rows if "split" in r]
        after = [s["shadow"] for s in split if "shadow" in s]
        done = [s for s in after if "error" not in s]
        errors = Counter(s["error"] for s in after if "error" in s)
        paired = [s for s in split if "error" not in s and "input_tokens" in s.get("shadow", {})]
        both = [s["input_tokens"] + s["shadow"]["input_tokens"] for s in paired]
        lines += [
            "",
            f"**`split`, background shadow call** (not on the request path): "
            f"p50 {_fmt(_quantile([s['ms'] for s in done], 0.5))} / p95 "
            f"{_fmt(_quantile([s['ms'] for s in done], 0.95))} ms, "
            f"{sum(errors.values())} errors{f' {dict(errors)}' if errors else ''}, input tokens / eval "
            f"{statistics.mean(s['input_tokens'] for s in done) if done else 0:.0f} "
            f"(both calls together {statistics.mean(both) if both else 0:.0f}). "
            f"The timed call started while the previous case's background call was still "
            f"running in {sum(1 for s in split if s.get('overlap'))}/{len(split)} cases.",
        ]
    lines.append("")
    compared = [m for m in meta["modes"] if m != "all"] if "all" in meta["modes"] else []
    for mode in compared:
        same = total = same_enforcing = total_enforcing = cases = 0
        for r in rows:
            base = r.get("all", {}).get("answers")
            other = dict(r.get(mode, {}).get("answers") or {})
            other.update(r.get(mode, {}).get("shadow", {}).get("answers") or {})
            if base is None or not other:
                continue
            cases += 1
            for qid, value in base.items():
                if qid not in other:
                    continue
                total += 1
                same += value == other[qid]
                if qid.split(".", 1)[0] in enforcing:
                    total_enforcing += 1
                    same_enforcing += value == other[qid]
        lines.append(
            f"**Same answer, `{mode}` vs `all`:** {same}/{total} questions "
            f"({(same / total * 100) if total else 0:.1f} %), enforcing questions "
            f"{same_enforcing}/{total_enforcing}, over {cases} cases."
        )
    lines += [
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
    parser.add_argument(
        "--modes", default="all,per_policy", help=f"l1-latency: comma-separated, of {MODES}"
    )
    parser.add_argument("--region-note", help="l1-latency: where this ran (e.g. Azure West Europe)")
