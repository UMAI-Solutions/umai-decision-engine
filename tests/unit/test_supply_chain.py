"""Supply-chain rules (ADR-0049) that a later edit could quietly break: third-party code in CI
is pinned (actions by commit SHA, scanner images and base images by digest), and every
accepted image-scan finding has a reason and an expiry date."""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
MAX_ACCEPTANCE = dt.timedelta(days=90)


def test_workflows_exist() -> None:
    assert {p.name for p in WORKFLOWS} >= {"ci.yml", "supply-chain.yml"}


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_actions_are_pinned_by_commit_sha(workflow: Path) -> None:
    for line in workflow.read_text(encoding="utf-8").splitlines():
        found = re.search(r"uses:\s*(\S+)(.*)", line)
        if not found or found.group(1).startswith("./"):
            continue
        ref = found.group(1).partition("@")[2]
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{workflow.name}: {line.strip()}"
        # Dependabot keeps the version comment in step with the SHA.
        assert re.match(r"\s*# v\d", found.group(2)), f"{workflow.name}: {line.strip()}"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_scanner_images_are_pinned_by_digest(workflow: Path) -> None:
    env = yaml.safe_load(workflow.read_text(encoding="utf-8")).get("env") or {}
    for name, image in env.items():
        if name.endswith("_IMAGE"):
            assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{workflow.name}: {name}"


def test_base_images_are_pinned_by_digest() -> None:
    lines = (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines()
    bases = [line.split()[1] for line in lines if line.startswith("FROM ")]
    assert bases
    for base in bases:
        assert re.search(r"@sha256:[0-9a-f]{64}$", base), base


def test_every_accepted_finding_has_a_statement_and_an_expiry() -> None:
    ignore = yaml.safe_load((ROOT / ".trivyignore.yaml").read_text(encoding="utf-8")) or {}
    for kind, entries in ignore.items():
        for entry in entries or []:
            label = f"{kind}: {entry.get('id')}"
            assert str(entry.get("statement") or "").strip(), label
            expires = entry.get("expired_at")
            assert isinstance(expires, dt.date), label
            assert expires - dt.date.today() <= MAX_ACCEPTANCE, label
