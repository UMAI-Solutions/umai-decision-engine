"""deploy/vm/umai.sh (ADR-0032): the console gate switch, generated secrets, files the running
containers must follow, zero-downtime deploys, backups and the restore test (ADR-0052). The
script is sourced with stubbed commands; nothing touches Docker."""

from __future__ import annotations

import json
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


def test_a_rotated_actor_key_replaces_the_console(tmp_path) -> None:
    seen = tmp_path / "in-container" / "run" / "secrets"
    seen.mkdir(parents=True)
    (tmp_path / "actor.pem").write_text("new key\n", encoding="utf-8", newline="\n")
    (seen / "actor.pem").write_text("old key\n", encoding="utf-8", newline="\n")
    (tmp_path / "plan").write_text("Container umai-console-1 Running\n", encoding="utf-8")

    check = 'needs_new console "$(cat "$UMAI_RUN_DIR/plan")" && echo new || echo same'
    result = _run(tmp_path, check)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "new\n"  # a single-file mount keeps the old key: a new container

    (seen / "actor.pem").write_text("new key\n", encoding="utf-8", newline="\n")
    assert _run(tmp_path, check).stdout == "same\n"
    calls = (tmp_path / "calls").read_text(encoding="utf-8")
    assert "restart" not in calls  # nothing is restarted in place any more (ADR-0052)


# ------------------------------------------------------------ L1b decision provider (ADR-0040)

KEY = "sk-proj-synthetic_Key-0123"  # synthetic: the shape of an OpenAI key, never a real one


def _settings(output: str) -> dict[str, object]:
    lines = dict(line.split("=", 1) for line in output.splitlines() if line)
    return {name: json.loads(value) for name, value in lines.items()}


def test_a_stored_openai_key_gives_the_engine_its_decision_provider(tmp_path) -> None:
    (tmp_path / "secrets.env").write_text(
        f"UMAI_OPENAI_API_KEY='{KEY}'\n", encoding="utf-8", newline="\n"
    )
    result = _run(tmp_path, "decision_env")
    assert result.returncode == 0, result.stderr
    assert _settings(result.stdout) == {
        "UMAI_DE_DECISION_PROVIDERS": {
            "openai": {
                "adapter": "openai_decisions",
                "base_url": "https://api.openai.com/v1",
                "model": "gpt-6-luna",
            }
        },
        "UMAI_DE_DECISION_PROVIDER_KEYS": {"openai": KEY},
    }

    eu = '{"openai":{"adapter":"openai_decisions","base_url":"https://eu.api.openai.com/v1"}}'
    result = _run(tmp_path, "decision_env", config=f"UMAI_DECISION_PROVIDERS='{eu}'\n")
    assert _settings(result.stdout)["UMAI_DE_DECISION_PROVIDERS"] == json.loads(eu)


def test_without_a_key_the_engine_gets_no_decision_settings(tmp_path) -> None:
    (tmp_path / "secrets.env").write_text("UMAI_API_TOKEN='x'\n", encoding="utf-8", newline="\n")
    result = _run(tmp_path, "decision_env; echo end")
    assert result.returncode == 0, result.stderr
    assert result.stdout == "end\n"  # an empty value would stop the engine: nothing is written
    assert "no UMAI_OPENAI_API_KEY stored" in result.stderr


def test_a_key_that_would_break_the_settings_stops_the_script(tmp_path) -> None:
    (tmp_path / "secrets.env").write_text(
        "UMAI_OPENAI_API_KEY='sk-a,\"b'\n", encoding="utf-8", newline="\n"
    )
    result = _run(tmp_path, 'out="$(decision_env)"; echo reached')
    assert result.returncode == 1 and "reached" not in result.stdout
    assert "sk-a" not in result.stderr  # the key is never echoed


def test_the_webhook_key_and_the_console_address_reach_the_policy_service(tmp_path) -> None:
    key = "c3ludGhldGljLXdlYmhvb2stbWFzdGVyLWtleS0wMDAwMDA="  # synthetic
    (tmp_path / "secrets.env").write_text(
        f"UMAI_WEBHOOK_MASTER_KEY='{key}'\n", encoding="utf-8", newline="\n"
    )
    result = _run(tmp_path, "webhook_env", config="UMAI_HOSTNAME=umai.example.com\n")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        "UMAI_DE_CONSOLE_URL=https://umai.example.com",
        f"UMAI_DE_WEBHOOK_MASTER_KEY={key}",
    ]
    (tmp_path / "secrets.env").write_text("UMAI_API_TOKEN='x'\n", encoding="utf-8", newline="\n")
    result = _run(tmp_path, "webhook_env", config="UMAI_HOSTNAME=umai.example.com\n")
    assert result.stdout == "UMAI_DE_CONSOLE_URL=https://umai.example.com\n"  # webhooks off


# ------------------------------------------------------------ zero-downtime deploys (ADR-0052)

# What Compose 2.40.3 printed for a dry run on the single-VM stack after new images were loaded
# (`up`; `create`, which umai.sh asks, prints the same decisions without the start lines).
DRY_RUN_NEW_IMAGES = """\
 DRY-RUN MODE -  Container umai-postgres-1  Running
 DRY-RUN MODE -  Container umai-seed-1  Recreate
 DRY-RUN MODE -  Container umai-seed-1  Recreated
 DRY-RUN MODE -  Container umai-policy-3  Recreate
 DRY-RUN MODE -  Container umai-policy-3  Recreated
 DRY-RUN MODE -  Container umai-gateway-3  Recreate
 DRY-RUN MODE -  Container umai-engine-3  Recreate
 DRY-RUN MODE -  Container umai-gateway-3  Recreated
 DRY-RUN MODE -  Container umai-engine-3  Recreated
 DRY-RUN MODE -  Container umai-console-3  Recreate
 DRY-RUN MODE -  Container umai-console-3  Recreated
 DRY-RUN MODE -  Container umai-caddy-1  Running
 DRY-RUN MODE -  Container 27e0cf796359_umai-policy-3  Starting
 DRY-RUN MODE -  Container umai-policy-3  Healthy
"""
# A changed network (the fixed subnet of ADR-0046): Compose stops everything and removes it.
DRY_RUN_NETWORK = """\
 DRY-RUN MODE -  Container umai-engine-1  Stopping
 DRY-RUN MODE -  Container umai-engine-1  Stopped
 DRY-RUN MODE -  Network umai_default  Removed
 DRY-RUN MODE -  Container umai-engine-1  Running
"""
DRY_RUN_FIRST_START = """\
 DRY-RUN MODE -  Network umai_default  Creating
 DRY-RUN MODE -  Network umai_default  Created
 DRY-RUN MODE -  Volume "umai_postgres"  Creating
 DRY-RUN MODE -  Container umai-postgres-1  Creating
"""

# up() with its surroundings stubbed: the dry run prints $UMAI_RUN_DIR/dry-run, Caddy runs, and
# roll, compose and docker record what they would do.
UP_STUBS = (
    "secrets() { :; }\ncaddy_files() { :; }\nmounted_changed() { return 1; }\n"
    'compose() { echo "compose $*" >>"$UMAI_RUN_DIR/calls"; '
    'if [[ "$1" == --dry-run ]]; then cat "$UMAI_RUN_DIR/dry-run"; fi; }\n'
    'running() { [[ "$1" == caddy ]] && echo caddy-id; true; }\n'
    'roll() { echo "roll $1" >>"$UMAI_RUN_DIR/calls"; }\n'
    'docker() { echo "docker $*" >>"$UMAI_RUN_DIR/calls"; }\n'
)


def _calls(tmp_path: Path) -> list[str]:
    path = tmp_path / "calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_new_images_replace_every_service_side_by_side_the_policy_service_first(
    tmp_path,
) -> None:
    (tmp_path / "dry-run").write_text(DRY_RUN_NEW_IMAGES, encoding="utf-8", newline="\n")
    result = _run(tmp_path, UP_STUBS + "up", config="UMAI_GATEWAY=true\n")
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    rolls = [c for c in calls if c.startswith("roll ")]
    assert rolls == ["roll policy", "roll engine", "roll gateway", "roll console"]
    assert calls.index("roll console") < calls.index(
        "compose up -d --no-build --no-recreate --remove-orphans"
    )
    assert "compose rm -f -s seed" in calls  # the one-shot runs again with the new image
    # Nothing running is recreated in place, and Caddy is not touched for an image deploy.
    assert not [c for c in calls if c.startswith("compose up") and "--no-recreate" not in c]
    assert not [c for c in calls if "caddy reload" in c or "restart" in c]


@pytest.mark.parametrize(
    ("dry_run", "named"),
    [
        (DRY_RUN_NETWORK, "the network umai_default changes"),
        (" DRY-RUN MODE -  Container umai-postgres-1  Recreate\n", "the postgres container"),
        (" DRY-RUN MODE -  Container umai-caddy-1  Recreate\n", "the caddy container"),
    ],
)
def test_a_change_that_needs_downtime_is_refused_with_the_way_out(tmp_path, dry_run, named) -> None:
    (tmp_path / "dry-run").write_text(dry_run, encoding="utf-8", newline="\n")
    result = _run(tmp_path, UP_STUBS + "up; echo reached")
    assert result.returncode == 3 and "reached" not in result.stdout
    assert "cannot be deployed without downtime" in result.stderr
    assert named in result.stderr and "umai.sh up --recreate" in result.stderr
    assert [c for c in _calls(tmp_path) if not c.startswith("compose --dry-run")] == []


def test_a_first_start_is_no_network_change(tmp_path) -> None:
    (tmp_path / "dry-run").write_text(DRY_RUN_FIRST_START, encoding="utf-8", newline="\n")
    result = _run(tmp_path, UP_STUBS + "running() { true; }\nup")  # nothing runs yet
    assert result.returncode == 0, result.stderr
    assert "compose up -d --no-build --remove-orphans" in _calls(tmp_path)


def test_recreate_takes_every_profile_down_and_starts_again(tmp_path) -> None:
    result = _run(tmp_path, UP_STUBS + "up --recreate")
    assert result.returncode == 0, result.stderr
    calls = _calls(tmp_path)
    assert calls[0] == (
        f"docker compose --project-directory {SCRIPT.parent.as_posix()} --env-file "
        f"{(tmp_path / 'config.env').as_posix()} --profile gateway down --remove-orphans"
    )
    assert "compose up -d --no-build --remove-orphans" in calls
    assert not [c for c in calls if c.startswith("compose --dry-run")]


# roll() against a fake Docker: $UMAI_RUN_DIR/running lists the service's containers (Compose
# appends the new one), $UMAI_RUN_DIR/health is what Docker's health check reports.
ROLL_STUBS = (
    "sleep() { :; }\n"
    'running() { cat "$UMAI_RUN_DIR/running"; }\n'
    'compose() { echo "compose $*" >>"$UMAI_RUN_DIR/calls"; '
    'if [[ "$1" == up ]]; then echo new-1 >>"$UMAI_RUN_DIR/running"; fi; }\n'
    'docker() { case "$*" in\n'
    '  *State.Health*) echo "inspect $4" >>"$UMAI_RUN_DIR/calls"; cat "$UMAI_RUN_DIR/health" ;;\n'
    '  *.Name*) echo "/umai-$4" ;;\n'
    '  *) echo "docker $*" >>"$UMAI_RUN_DIR/calls" ;;\n'
    "esac; }\n"
)


def test_the_old_container_stops_only_after_the_new_one_is_healthy(tmp_path) -> None:
    (tmp_path / "running").write_text("old-1\n", encoding="utf-8", newline="\n")
    (tmp_path / "health").write_text("healthy\n", encoding="utf-8", newline="\n")
    result = _run(tmp_path, ROLL_STUBS + "roll engine")
    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        "compose up -d --no-build --no-deps --no-recreate --scale engine=2 engine",
        "inspect new-1",
        "docker stop old-1",  # graceful, within the service's stop_grace_period
        "docker rm old-1",
    ]
    assert "engine: replaced by umai-new-1" in result.stdout


def test_an_unhealthy_new_container_is_removed_and_the_old_one_keeps_serving(tmp_path) -> None:
    (tmp_path / "running").write_text("old-1\n", encoding="utf-8", newline="\n")
    (tmp_path / "health").write_text("unhealthy\n", encoding="utf-8", newline="\n")
    result = _run(tmp_path, ROLL_STUBS + "roll policy; echo reached")
    assert result.returncode == 1 and "reached" not in result.stdout
    calls = _calls(tmp_path)
    assert "docker stop new-1" in calls and "docker rm new-1" in calls
    assert not [c for c in calls if "old-1" in c]
    assert "did not become healthy (unhealthy)" in result.stderr
    assert "the running containers keep serving" in result.stderr


def test_caddy_is_reloaded_only_when_its_files_changed(tmp_path) -> None:
    body = 'caddy_files; echo "changed=$CADDY_CHANGED"'
    assert _run(tmp_path, body).stdout == "changed=1\n"  # first time: a new Caddyfile
    staged = tmp_path / "caddy" / "Caddyfile.new"
    assert staged.read_bytes() == (SCRIPT.parent / "Caddyfile").read_bytes()
    assert not (tmp_path / "caddy" / "Caddyfile").exists()  # not before Caddy validated it

    # Caddy refuses it: what the running Caddy reads stays, the new file is dropped.
    result = _run(tmp_path, "compose() { return 1; }\ncaddy_files; caddy_validate; echo reached")
    assert result.returncode == 1 and "reached" not in result.stdout
    assert "the shipped Caddyfile is invalid; nothing was changed" in result.stderr
    assert not staged.exists()

    result = _run(tmp_path, "compose() { :; }\ncaddy_files; caddy_validate; caddy_take")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "caddy" / "Caddyfile").exists() and not staged.exists()
    assert _run(tmp_path, body).stdout == "changed=\n"  # the same Caddyfile: nothing to do

    gate = 'put() { cat >"$1"; }\nconsole_gate; echo "changed=$CADDY_CHANGED"'  # no chown here
    config = "UMAI_CONSOLE_BASIC_AUTH=false\n"
    assert _run(tmp_path, gate, config=config).stdout == "changed=1\n"
    assert _run(tmp_path, gate, config=config).stdout == "changed=\n"
    assert "vars console_gate oidc" in (tmp_path / "caddy" / "console-gate.caddy").read_text()


# ------------------------------------------------------------ backups and restore test (ADR-0052)


def test_the_dump_keeps_its_grants_and_the_archive_holds_the_applications(tmp_path) -> None:
    body = (
        'compose() { echo "compose $*" >>"$UMAI_RUN_DIR/calls"; }\n'
        'docker() { echo "docker $*" >>"$UMAI_RUN_DIR/calls"; }\n'
        'make_archive "$UMAI_RUN_DIR/archive.tar.gz"'
    )
    result = _run(tmp_path, body, config="UMAI_APPLICATIONS=/opt/umai/applications\n")
    assert result.returncode == 0, result.stderr
    dump, tar = _calls(tmp_path)
    # Without its GRANTs a restored store denies the application role (the migrations grant).
    assert dump == "compose exec -T postgres pg_dump -U umai_owner -d umai --no-owner"
    assert "-v /opt/umai/applications:/a:ro" in tar
    assert tar.endswith("tar -czf /out/archive.tar.gz -C / p a db/umai.sql.gz")


SYNTHETIC_DUMP = """\
SET statement_timeout = 0;
COPY public.tenants (id, slug, name) FROM stdin;
00000000-0000-0000-0000-000000000001\tconsole\tConsole
00000000-0000-0000-0000-000000000002\tpilot-1\tline one\\nline two
\\.

COPY public.drafts (id, body) FROM stdin;
\\.

COPY public."user" (id) FROM stdin;
00000000-0000-0000-0000-000000000003
\\.

GRANT SELECT,INSERT ON TABLE public.usage_events TO umai_app;
"""


def test_rows_are_counted_from_the_dump_itself(tmp_path) -> None:
    (tmp_path / "dump.sql").write_text(SYNTHETIC_DUMP, encoding="utf-8", newline="\n")
    result = _run(tmp_path, 'dump_counts <"$UMAI_RUN_DIR/dump.sql"')
    assert result.returncode == 0, result.stderr
    # An escaped newline inside a value is one row; an empty table has 0.
    assert result.stdout.splitlines() == ["tenants 2", "drafts 0", "user 1"]


SAS = "sv=2025-01-05&sr=b&sp=r&sig=c3ludGhldGlj"  # synthetic, the shape of a read-only SAS
BLOB = "umai-backup-20261031T023000Z.tar.gz"


@pytest.mark.parametrize("name", ["../../etc/passwd", "umai-backup-latest.tar.gz", "x.tar.gz"])
def test_only_backup_blob_names_are_fetched(tmp_path, name) -> None:
    body = f'curl() {{ echo curl >>"$UMAI_RUN_DIR/calls"; }}\nfetch_backup "{name}" out <<<"{SAS}"'
    result = _run(tmp_path, body)
    assert result.returncode == 2
    assert "neither a file here nor a backup blob name" in result.stderr
    assert _calls(tmp_path) == []


def test_a_blob_is_fetched_with_the_sas_from_stdin_which_is_never_echoed(tmp_path) -> None:
    body = (
        'curl() { echo "curl $*" >>"$UMAI_RUN_DIR/calls"; }\n'
        f'fetch_backup {BLOB} "$UMAI_RUN_DIR/b.tar.gz" <<<"{SAS}"'
    )
    result = _run(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert _calls(tmp_path) == [
        f"curl -sf -o {(tmp_path / 'b.tar.gz').as_posix()} "
        f"https://backups.blob.core.windows.net/backups/{BLOB}?{SAS}"
    ]
    assert SAS not in result.stdout + result.stderr

    result = _run(tmp_path, f"fetch_backup {BLOB} out </dev/null; echo reached")
    assert result.returncode == 2 and "reached" not in result.stdout
    assert "pipe a read-only SAS" in result.stderr


def test_a_failed_restore_test_removes_its_scratch_project(tmp_path) -> None:
    body = (
        'docker() { echo "docker $*" >>"$UMAI_RUN_DIR/calls"; }\n'
        "restore_test umai-backup-not-a-stamp.tar.gz"
    )
    result = _run(tmp_path, body)
    assert result.returncode == 2
    down = [c for c in _calls(tmp_path) if " down " in c]
    assert len(down) == 1 and "-p umai-restore-" in down[0] and "down -v" in down[0]
