"""Suite `leak`: does any raw value appear in responses, traces, logs or stored records? Must be 0.

Every dataset case with entities is evaluated through the full pipeline (`Evaluator`) against
a bundle (default: the active `gr-demo-data-protection`), with trace on and every log record
captured at DEBUG. A raw value — as written, or its digits only when it has 7+ digits — found
in the response or the logs is a leak. Separately, with the decision: when it allows the
content, the caller forwards the masked text (or the original text when nothing was masked),
so a raw value there would reach the destination. That is an L0 miss with a real consequence —
reported, but not a leak of the engine's own outputs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from evals.common import (
    EVAL_HMAC_KEY,
    L0_DATASET,
    ROOT,
    load_cases,
    report_dir,
    run_info,
    write_results,
)
from evals.suites.leak_errors import report_lines as error_report_lines
from evals.suites.leak_errors import run_error_paths
from umai_decision_engine.contracts.evaluation import (
    CONTRACT_VERSION,
    BundleRef,
    ChatMessage,
    Content,
    Destination,
    EvaluationRequest,
    Flags,
)
from umai_decision_engine.events.recorder import MemoryRecorder
from umai_decision_engine.l0.detector import EntityDetector
from umai_decision_engine.pipeline.orchestrator import Evaluator
from umai_decision_engine.policy.sources import DirectorySource
from umai_decision_engine.policy.store import BundleStore
from umai_decision_engine.settings import Settings

NAME = "leak"
DEFAULT_BUNDLE = "gr-demo-data-protection"
PARTS = ("pipeline", "errors")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--leak-parts", default=",".join(PARTS), help="leak: pipeline and/or errors (S0-4)"
    )


_NON_DIGIT = re.compile(r"\D")


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


def _forms(value: str) -> set[str]:
    forms = {value}
    digits = _NON_DIGIT.sub("", value)
    if len(digits) >= 7:
        forms.add(digits)
    return forms


async def _evaluate_all(
    cases: list[dict], bundle_id: str, version: int | None
) -> tuple[list[dict], Counter[str]]:
    store = BundleStore(DirectorySource(ROOT / "policies"), public_keys={}, require_signed=False)
    store.reload(force=True)
    if store.get(bundle_id, version) is None:
        raise SystemExit(f"bundle {bundle_id} version {version or 'active'} is not loaded")
    detector = EntityDetector(lambda _tenant: EVAL_HMAC_KEY, threads=1)
    await asyncio.to_thread(detector.warm_up)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        env="test",
        max_timeout_ms=60_000,
        default_timeout_ms=30_000,
        require_signed_bundles=False,
        allow_inline_bundles=False,
    )
    recorder = MemoryRecorder()  # the records the engine would store (ADR-0041)
    evaluator = Evaluator(store, detector, settings, recorder=recorder)

    capture = _Capture()
    capture.setFormatter(logging.Formatter("%(name)s %(levelname)s %(message)s"))
    root, engine_logger = logging.getLogger(), logging.getLogger("umai.de")
    previous_levels = (root.level, engine_logger.level)
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)
    engine_logger.setLevel(logging.DEBUG)  # every engine record, as production could emit it
    rows: list[dict] = []
    actions: Counter[str] = Counter()
    try:
        for case in cases:
            capture.messages.clear()
            recorder.records.clear()
            request = EvaluationRequest(
                contract_version=CONTRACT_VERSION,
                request_id=f"leak:{case['case_id']}",
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                tenant_id="eval",
                bundle=BundleRef(id=bundle_id, version=version),
                phase="PRE_LLM",
                content=Content(
                    messages=[ChatMessage(role="user", content=case["text"])],
                    focus="LAST_USER_MESSAGE",
                ),
                destination=Destination(app="chatgpt.com", **{"class": "external_llm"}),
                flags=Flags(trace=True),
            )
            response = await evaluator.evaluate(request)
            await evaluator.l1b.drain()  # shadow outcomes are records too
            actions[str(response.decision.action)] += 1
            payload = response.model_dump(mode="json", exclude_none=True)
            redacted = (payload.get("output_modifications") or {}).pop("redacted_text", None)
            outputs = json.dumps(payload, ensure_ascii=False)
            logs = "\n".join(capture.messages)
            records = "\n".join(r.model_dump_json() for r in recorder.records)
            row = {
                "case_id": case["case_id"],
                "action": str(response.decision.action),
                "response": [],
                "logs": [],
                "records": [],
                "reaches_destination": [],
            }
            # What the caller forwards when the decision allows: the masked text, or the
            # original text when nothing was masked. Nothing is forwarded on a block.
            forwarded = ""
            if response.decision.allowed:
                forwarded = redacted if redacted is not None else case["text"]
            for expected in case["expected"]:
                forms = _forms(case["text"][expected["start"] : expected["end"]])
                if any(f in outputs for f in forms):
                    row["response"].append(expected["type"])
                if any(f in logs for f in forms):
                    row["logs"].append(expected["type"])
                if any(f in records for f in forms):
                    row["records"].append(expected["type"])
                if forwarded and any(f in forwarded for f in forms):
                    row["reaches_destination"].append(expected["type"])
            rows.append(row)
    finally:
        root.removeHandler(capture)
        root.setLevel(previous_levels[0])
        engine_logger.setLevel(previous_levels[1])
        detector.close()
    return rows, actions


def run(args: argparse.Namespace) -> Path:
    bundle_id = args.bundle_id or DEFAULT_BUNDLE
    cases = [c for c in load_cases(L0_DATASET, args.split) if c["expected"]]
    version = getattr(args, "bundle_version", None)
    parts = set(getattr(args, "leak_parts", ",".join(PARTS)).split(","))
    rows, actions = ([], Counter())
    if "pipeline" in parts:
        rows, actions = asyncio.run(_evaluate_all(cases, bundle_id, version))
    probes = run_error_paths(cases) if "errors" in parts else []

    info = run_info(NAME, args.split, args.tag, L0_DATASET)
    directory = report_dir(info)
    write_results(directory, rows)

    response_leaks = [r["case_id"] for r in rows if r["response"]]
    log_leaks = [r["case_id"] for r in rows if r["logs"]]
    record_leaks = [r["case_id"] for r in rows if r["records"]]
    reaching = [r for r in rows if r["reaches_destination"]]
    error_leaks = [p for p in probes if p.leaked]
    verdict = "PASS" if not (response_leaks or log_leaks or record_leaks or error_leaks) else "FAIL"
    lines = [
        f"# leak — {info.tag}",
        "",
        *info.header_lines(),
        f"- **Bundle:** `{bundle_id}` ({f'v{version}' if version else 'active version'}), "
        "phase PRE_LLM, destination external_llm, "
        "trace on, logs captured at DEBUG",
        f"- **Cases:** {len(rows)} with at least one entity"
        + ("" if "pipeline" in parts else " (pipeline part not run)"),
        f"- **Error paths:** {len(probes)} probes"
        + ("" if "errors" in parts else " (error part not run)"),
        "",
        f"## Verdict: {verdict}",
        "",
        "| Where | Cases with a raw value | Must be |",
        "|---|---|---|",
        f"| Response and trace (outside the masked text) | {len(response_leaks)} | 0 |",
        f"| Logs | {len(log_leaks)} | 0 |",
        f"| Stored records (decision events, L1b outcomes, usage) | {len(record_leaks)} | 0 |",
        f"| Error answers and their logs (probes) | {len(error_leaks)} | 0 |",
        "",
        "Decisions: " + ", ".join(f"{a} {n}" for a, n in sorted(actions.items())),
        "",
        f"## Values that would reach the destination: {len(reaching)} cases",
        "",
        "The decision allowed the content and the value was not masked, so the caller would send",
        "it to the external LLM. Not a leak of the engine's own outputs: each one is an L0 miss",
        "(or an entity type L0 does not support yet) with a real consequence.",
        "",
    ]
    if not reaching:
        lines.append("None.")
    for row in reaching:
        types = ", ".join(row["reaches_destination"])
        lines.append(f"- {row['case_id']}: {types} (decision {row['action']})")
    if response_leaks:
        lines += ["", f"Response leaks: {', '.join(response_leaks)}"]
    if log_leaks:
        lines += ["", f"Log leaks: {', '.join(log_leaks)}"]
    if record_leaks:
        lines += ["", f"Record leaks: {', '.join(record_leaks)}"]
    if probes:
        lines += ["", *error_report_lines(probes)]
    (directory / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return directory
