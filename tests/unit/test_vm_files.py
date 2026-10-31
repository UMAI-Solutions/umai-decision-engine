"""deploy/vm (ADR-0032, ADR-0052): the Compose files and the Caddyfile keep what zero-downtime
deploys and the restore test rely on. Static checks; nothing is started."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

VM = Path(__file__).resolve().parents[2] / "deploy" / "vm"
ROLLING = ("policy", "engine", "gateway", "console")  # umai.sh ROLLING


def _compose(name: str) -> dict:
    return yaml.safe_load((VM / name).read_text(encoding="utf-8"))


def test_umai_sh_replaces_the_services_the_compose_file_can_run_twice() -> None:
    script = (VM / "umai.sh").read_text(encoding="utf-8")
    assert f"ROLLING=({' '.join(ROLLING)})" in script
    services = _compose("docker-compose.yml")["services"]
    for name in ROLLING:
        service = services[name]
        # Two containers of the service at once: no fixed name, address or host port.
        assert "container_name" not in service and "ports" not in service, name
        assert "networks" not in service, name
        health = service["healthcheck"]
        assert health["start_interval"] == "2s", name  # a deploy goes on as soon as it is ready
        assert "stop_grace_period" in service, name  # the old one finishes its requests


def test_the_console_reaches_the_backends_through_caddy() -> None:
    console = _compose("docker-compose.yml")["services"]["console"]["environment"]
    assert console["UMAI_ENGINE_URL"] == "http://caddy:9100"
    assert console["UMAI_CONTROL_URL"] == "http://caddy:9200"


def test_caddy_reads_one_directory_and_retries_request_bodies() -> None:
    caddy = _compose("docker-compose.yml")["services"]["caddy"]
    assert caddy["volumes"][0] == "${UMAI_RUN_DIR:-/run/umai}/caddy:/etc/caddy:ro"
    # 2.11.4 or later: a retry after a refused connection keeps the body (2.10 sent it empty).
    version = re.fullmatch(r"caddy:(\d+)\.(\d+)\.(\d+)-alpine", caddy["image"])
    assert version is not None
    assert tuple(int(part) for part in version.groups()) >= (2, 11, 4)


def test_every_route_goes_to_the_containers_through_the_upstream_snippet() -> None:
    caddyfile = (VM / "Caddyfile").read_text(encoding="utf-8")
    snippet = caddyfile[caddyfile.index("(upstream) {") :]
    snippet = snippet[: snippet.index("\n}\n")]
    for line in ("dynamic a {args[0]} {args[1]}", "refresh 1s", "lb_try_duration", "keepalive off"):
        assert line in snippet
    # No service named directly: a reverse_proxy without a block would bypass the retries.
    assert not re.search(r"reverse_proxy\s+\S+:\d+", caddyfile)
    blocks = caddyfile.count("reverse_proxy {")
    imports = re.findall(r"import upstream (?:engine|policy|gateway|console) \d+", caddyfile)
    assert blocks == len(imports)
    assert blocks == 7  # gateway, hooks, engine, brand, console, two internal listeners
    assert "import /etc/caddy/console-gate.caddy" in caddyfile


def test_the_restore_project_is_closed_and_uses_the_live_images() -> None:
    scratch = _compose("restore-test.yml")
    live = _compose("docker-compose.yml")["services"]
    assert scratch["networks"]["scratch"]["internal"] is True  # nothing calls out
    assert scratch["services"]["files"]["network_mode"] == "none"
    for name, service in scratch["services"].items():
        assert "ports" not in service, name
        if name in live:
            assert service["image"] == live[name]["image"], name
    # Healthy only once the real server listens: over the socket, pg_isready already answers
    # during the init scripts, and a restore started then failed on umai-beta (2026-10-31).
    assert "pg_isready -h 127.0.0.1" in scratch["services"]["postgres"]["healthcheck"]["test"][1]
    policy = scratch["services"]["policy"]["environment"]
    assert policy["UMAI_DE_RECORDS_RETENTION_INTERVAL_S"] == "0"  # no deletes on restored data
    for forbidden in ("WEBHOOK", "IYZICO", "BILLING", "SIGNING_KEY"):
        assert not [key for key in policy if forbidden in key], forbidden


def test_ship_sh_delivers_the_restore_project() -> None:
    ship = (VM / "ship.sh").read_text(encoding="utf-8")
    assert "vm/restore-test.yml" in ship


def test_the_api_documentation_ships_in_the_image_and_caddy_serves_it() -> None:
    """ADR-0054: the image builds the site, the seed copies it, Caddy serves it read-only."""

    root = VM.parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    pin = re.search(r'docs = \["markdown-it-py==([0-9.]+)"\]', pyproject)
    assert pin and f"pip install markdown-it-py=={pin.group(1)}" in dockerfile
    assert "COPY --from=docs --chown=umai /site /app/docs-site" in dockerfile
    ignore = (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "!docs/api" in ignore and "!dev/api_docs.py" in ignore

    compose = _compose("docker-compose.yml")
    seed = compose["services"]["seed"]
    assert "docs:/data/docs" in seed["volumes"]
    assert "seed-docs.sh" in " ".join(seed["command"])
    assert "docs:/srv/docs:ro" in compose["services"]["caddy"]["volumes"]
    assert "docs" in compose["volumes"]
    assert "vm/seed-docs.sh" in (VM / "ship.sh").read_text(encoding="utf-8")

    caddyfile = (VM / "Caddyfile").read_text(encoding="utf-8")
    assert "handle_path /docs/* {" in caddyfile and "root * /srv/docs" in caddyfile
    assert re.search(r"@fastapi_docs path /redoc /openapi\.json", caddyfile)
