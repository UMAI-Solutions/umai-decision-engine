"""deploy/vm/seed-policies.sh: an empty volume gets every seed bundle, a running one only the
bundles it lacks (the templates of a later release, ADR-0056 §3); existing ones are never
touched. Runs the script with a stand-in for the signing CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "vm" / "seed-policies.sh"
SH = shutil.which("sh")

pytestmark = pytest.mark.skipif(SH is None, reason="needs a POSIX sh")


def _bundle(root: Path, name: str, marker: str) -> None:
    version = root / name / "1"
    version.mkdir(parents=True)
    (version / "bundle.yaml").write_text(f"id: {name}  # {marker}\n", encoding="utf-8")
    (root / name / "active").write_text("1\n", encoding="utf-8")


def _run(tmp_path: Path, seed: Path, target: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "python"  # "python -m … sign <dir> …" writes a manifest
    fake.write_text('#!/bin/sh\necho signed > "$4/manifest.json"\n', encoding="utf-8")
    fake.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "UMAI_SEED_SOURCE": str(seed),
        "UMAI_SEED_TARGET": str(target),
        "UMAI_SIGNING_KEY_ID": "k1",
    }
    done = subprocess.run(
        [SH, str(SCRIPT)], env=env, capture_output=True, text=True, check=True, timeout=60
    )
    return done.stdout


def test_only_missing_bundles_are_added_and_signed(tmp_path: Path) -> None:
    seed, target = tmp_path / "seed", tmp_path / "volume"
    _bundle(seed, "gr-demo", "seed")
    _bundle(seed, "tpl-blank", "seed")
    _bundle(target, "gr-demo", "edited by the policy service")
    out = _run(tmp_path, seed, target)
    assert "seed: added tpl-blank" in out and "added gr-demo" not in out
    assert "edited" in (target / "gr-demo" / "1" / "bundle.yaml").read_text(encoding="utf-8")
    assert not (target / "gr-demo" / "1" / "manifest.json").exists()
    assert (target / "tpl-blank" / "1" / "manifest.json").read_text().strip() == "signed"
    assert (target / "tpl-blank" / "active").is_file()
    assert sorted(p.name for p in target.iterdir()) == ["gr-demo", "tpl-blank"]  # no staging left
    assert "(0 added)" in _run(tmp_path, seed, target)  # a second run changes nothing
