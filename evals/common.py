"""Shared harness helpers: datasets, run metadata, report directories."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "evals" / "reports"
L0_DATASET = ROOT / "evals" / "datasets" / "l0-pii-tr" / "v2.jsonl"
EVAL_HMAC_KEY = b"umai-de-evals"  # value hashes are never inspected


def load_cases(path: Path, split: str) -> list[dict]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return cases if split == "all" else [c for c in cases if c["split"] == split]


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


@dataclass(frozen=True)
class RunInfo:
    suite: str
    split: str
    tag: str
    date: str
    commit: str
    dataset: Path
    dataset_sha256: str

    def header_lines(self) -> list[str]:
        try:
            dataset = self.dataset.relative_to(ROOT).as_posix()
        except ValueError:
            dataset = str(self.dataset)
        return [
            f"- **Date:** {self.date}",
            f"- **Git commit:** {self.commit}",
            f"- **Dataset:** `{dataset}` (sha256 `{self.dataset_sha256[:12]}`), "
            f"split `{self.split}`",
            f"- **Engine:** umai-decision-engine {_version('umai-decision-engine')}, "
            f"presidio-analyzer {_version('presidio-analyzer')}, "
            f"phonenumbers {_version('phonenumbers')}",
            f"- **Host:** {platform.system()} {platform.release()}, Python "
            f"{sys.version.split()[0]}, {platform.machine()}",
        ]


def run_info(suite: str, split: str, tag: str | None, dataset: Path) -> RunInfo:
    commit = _git("rev-parse", "--short", "HEAD") or "unknown"
    if _git("status", "--porcelain"):
        commit += " (uncommitted changes)"
    return RunInfo(
        suite=suite,
        split=split,
        tag=tag or split,
        date=datetime.now(UTC).date().isoformat(),
        commit=commit,
        dataset=dataset,
        dataset_sha256=hashlib.sha256(dataset.read_bytes()).hexdigest(),
    )


def report_dir(info: RunInfo) -> Path:
    directory = REPORTS / f"{info.date}-{info.suite}-{info.tag}"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_results(directory: Path, rows: list[dict]) -> None:
    """Per-case results (case ids, types, offsets; never text). Not committed (.gitignore)."""

    with (directory / "results.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f} %"
