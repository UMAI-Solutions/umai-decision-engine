"""Eval harness entry point (docs/evals/README.md).

python -m evals.run --suite l0-pii-tr [--split dev|test|all] [--tag NAME] [--bundle DIR]
python -m evals.run --suite leak      [--split dev|test|all] [--tag NAME] [--bundle-id ID]
                                      [--bundle-version N]
python -m evals.run --suite gateway-latency [--tag NAME]
python -m evals.run --suite pi-tr [--split dev|test|all] --detector heuristic[,<model>]
                                      [--model-dir DIR] [--threshold T]
python -m evals.run --suite masking-survival --model gpt-4.1-mini[,other] [--tag NAME]  # live
python -m evals.run --suite l1-latency [--limit 40] [--modes all,split] [--region-note TEXT]  # live
python -m evals.run --suite l1-cold-start [--processes 6] [--gap-s 8] [--region-note TEXT]  # live

Writes evals/reports/<date>-<suite>-<tag>/summary.md (committed) and results.jsonl (local).
Use the dev split while iterating; run the test split once at the end and never tune on it.
"""

from __future__ import annotations

import argparse
import logging
import sys

from evals.suites import (
    gateway_latency,
    l0_pii_tr,
    l1_cold_start,
    l1_latency,
    leak,
    masking_survival,
    pi_tr,
)

SUITES = {
    l0_pii_tr.NAME: l0_pii_tr.run,
    leak.NAME: leak.run,
    gateway_latency.NAME: gateway_latency.run,
    masking_survival.NAME: masking_survival.run,
    pi_tr.NAME: pi_tr.run,
    l1_latency.NAME: l1_latency.run,
    l1_cold_start.NAME: l1_cold_start.run,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--suite", required=True, choices=sorted(SUITES))
    parser.add_argument("--split", default="dev", choices=["dev", "test", "holdout", "all"])
    parser.add_argument("--tag", help="report name suffix (default: the split)")
    parser.add_argument("--bundle", help="l0-pii-tr: bundle version directory for the L0 config")
    parser.add_argument(
        "--sensitivity", choices=["L1", "L2", "L3", "L4"], help="l0-pii-tr: min score of a level"
    )
    parser.add_argument("--bundle-id", help="leak: bundle id (default gr-demo-data-protection)")
    parser.add_argument("--bundle-version", type=int, help="leak: version (default: active)")
    parser.add_argument("--model", help="masking-survival: comma-separated upstream models")
    parser.add_argument("--detector", help="pi-tr: comma-separated detectors (a+b = max of both)")
    parser.add_argument("--model-dir", help="pi-tr: folder with one sub-folder per model")
    parser.add_argument("--threshold", type=float, default=0.5, help="pi-tr: deployment threshold")
    l1_latency.add_arguments(parser)
    l1_cold_start.add_arguments(parser)
    args = parser.parse_args(argv)
    logging.getLogger("umai.de").setLevel(logging.WARNING)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    directory = SUITES[args.suite](args)
    print((directory / "summary.md").read_text(encoding="utf-8"))
    print(f"report: {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
