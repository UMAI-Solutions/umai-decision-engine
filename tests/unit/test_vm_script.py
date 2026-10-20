"""deploy/vm/umai.sh (ADR-0032): the console gate switch, generated secrets and files the running
containers must follow. The script is sourced with stubbed commands; nothing touches Docker."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "vm" / "umai.sh"
BASH = os.environ.get("UMAI_TEST_BASH") or shutil.which("bash")

# On Windows "bash" may resolve to the WSL launcher; set UMAI_TEST_BASH to Git Bash to run these.
pytestmark = pytest.mark.skipif(
    BASH is None or (sys.platform == "win32" and "UMAI_TEST_BASH" not in os.environ),
    reason="needs a POSIX bash",
)


def _run(tmp_path: Path, body: str, *, config: str = "") -> subprocess.CompletedProcess[str]:
    (tmp_path / "config.env").write_text(
        f"UMAI_BACKUP_ACCOUNT=backups\n{config}", encoding="utf-8", newline="\n"
    )
    script = (
        "set -euo pipefail\n"
        f'source "{SCRIPT.as_posix()}"\n'
        # Stubs: record compose calls; `exec` prints what the container sees.
        'compose() { echo "compose $*" >>"$UMAI_RUN_DIR/calls"; '
        'if [[ "$1" == exec ]]; then cat "$UMAI_RUN_DIR/in-container$5"; fi; }\n'
        f"{body}\n"
    )
    env = {
        **os.environ,
        "UMAI_CONFIG": (tmp_path / "config.env").as_posix(),
        "UMAI_SECRETS": (tmp_path / "secrets.env").as_posix(),
        "UMAI_RUN_DIR": tmp_path.as_posix(),
    }
    return subprocess.run(
        [BASH, "-c", script], env=env, capture_output=True, text=True, timeout=60, check=False
    )


@pytest.mark.parametrize("value", ["false", "true"])
def test_the_console_gate_switch_takes_true_or_false(tmp_path, value) -> None:
    result = _run(
        tmp_path,
        'echo "gate=$UMAI_CONSOLE_BASIC_AUTH"',
        config=f"UMAI_CONSOLE_BASIC_AUTH={value}\n",
    )
    assert result.returncode == 0, result.stderr
    assert f"gate={value}" in result.stdout


@pytest.mark.parametrize("value", ["False", "no", "yes", "1", "ture"])
def test_any_other_gate_value_stops_the_script(tmp_path, value) -> None:
    result = _run(tmp_path, "echo reached", config=f"UMAI_CONSOLE_BASIC_AUTH={value}\n")
    assert result.returncode == 1
    assert "reached" not in result.stdout
    assert "must be true or false" in result.stderr


def test_a_failing_generator_stores_nothing(tmp_path) -> None:
    (tmp_path / "secrets.env").write_text("UMAI_API_TOKEN='x'\n", encoding="utf-8", newline="\n")
    result = _run(
        tmp_path,
        "empty() { :; }\nensure_local_secret UMAI_SESSION_SECRET empty\necho reached",
    )
    assert result.returncode == 1 and "reached" not in result.stdout
    assert "could not generate UMAI_SESSION_SECRET" in result.stderr
    assert (tmp_path / "secrets.env").read_text(encoding="utf-8") == "UMAI_API_TOKEN='x'\n"

    result = _run(
        tmp_path, "fixed() { echo abc123; }\nensure_local_secret UMAI_SESSION_SECRET fixed"
    )
    assert result.returncode == 0, result.stderr
    assert "UMAI_SESSION_SECRET='abc123'" in (tmp_path / "secrets.env").read_text(encoding="utf-8")


def test_a_rotated_actor_key_restarts_the_console(tmp_path) -> None:
    seen = tmp_path / "in-container"
    (seen / "etc" / "caddy").mkdir(parents=True)
    (seen / "run" / "umai").mkdir(parents=True)
    (seen / "run" / "secrets").mkdir(parents=True)
    shutil.copy(SCRIPT.parent / "Caddyfile", seen / "etc" / "caddy" / "Caddyfile")
    for name in ("console-gate.caddy",):
        (tmp_path / name).write_text("gate\n", encoding="utf-8", newline="\n")
        (seen / "run" / "umai" / name).write_text("gate\n", encoding="utf-8", newline="\n")
    (tmp_path / "actor.pem").write_text("new key\n", encoding="utf-8", newline="\n")
    (seen / "run" / "secrets" / "actor.pem").write_text("old key\n", encoding="utf-8", newline="\n")

    result = _run(tmp_path, "follow_config")
    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "calls").read_text(encoding="utf-8").splitlines()
    assert "compose restart console" in calls
    assert "compose restart caddy" not in calls  # its files did not change

    (seen / "run" / "secrets" / "actor.pem").write_text("new key\n", encoding="utf-8", newline="\n")
    (tmp_path / "calls").unlink()
    assert _run(tmp_path, "follow_config").returncode == 0
    assert not any("restart" in c for c in (tmp_path / "calls").read_text().splitlines())
