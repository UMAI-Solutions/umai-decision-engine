"""Eval harness entry point (docs/evals/README.md).

python -m evals.run --suite l0-pii-tr [--split dev|test|all] [--tag NAME] [--bundle DIR]
python -m evals.run --suite leak      [--split dev|test|all] [--tag NAME] [--bundle-id ID]
                                      [--bundle-version N]

Writes evals/reports/<date>-<suite>-<tag>/summary.md (committed) and results.jsonl (local).
Use the dev split while iterating; run the test split once at the end and never tune on it.
"""

from __future__ import annotations

import argparse
import logging
import sys

from evals.suites import l0_pii_tr, leak

SUITES = {l0_pii_tr.NAME: l0_pii_tr.run, leak.NAME: leak.run}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.run")
    parser.add_argument("--suite", required=True, choices=sorted(SUITES))
    parser.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    parser.add_argument("--tag", help="report name suffix (default: the split)")
    parser.add_argument("--bundle", help="l0-pii-tr: bundle version directory for the L0 config")
    parser.add_argument("--bundle-id", help="leak: bundle id (default gr-demo-data-protection)")
    parser.add_argument("--bundle-version", type=int, help="leak: version (default: active)")
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
