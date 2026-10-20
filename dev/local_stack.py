"""Runs one service of the local console stack with the settings the three must share, without
touching anyone's .env files (development only; values already in the environment win):

- default tenant ``console`` and one key-registry directory (``.data/key-registry``), so API keys
  created in the console work against the engine (ADR-0023);
- the console's development actor key (``npm run actor-keygen`` in umai-control-center) as the
  policy service's ``UMAI_DE_ACTOR_PUBLIC_KEYS`` (contract credentials 0.3);
- one development engine token (``.data/dev-engine-token``, made on first use): with a key
  registry the engine is no longer open in development, so the console calls it as the
  operator, as it does in SaaS.

    python dev/local_stack.py engine | policy-service | console | gateway

The engine, the gateway (port 9300) and the policy service share ``.data/applications``, so
applications and providers saved through the policy service apply within seconds.

The console is looked for next to this repository; ``UMAI_CONSOLE_DIR`` overrides that.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / ".data"
CONSOLE = Path(os.environ.get("UMAI_CONSOLE_DIR") or ROOT.parent / "umai-control-center")


def engine_token() -> str:
    path = DATA / "dev-engine-token"
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:  # first service to start makes it
            handle.write(f"dev-{secrets.token_urlsafe(32)}\n")
    except FileExistsError:
        pass
    return path.read_text(encoding="utf-8").strip()


def shared_settings() -> None:
    os.environ.setdefault("UMAI_DE_DEFAULT_TENANT_ID", "console")
    os.environ.setdefault("UMAI_DE_KEY_REGISTRY_DIR", str(DATA / "key-registry"))
    os.environ.setdefault("UMAI_DE_APPLICATIONS_DIR", str(DATA / "applications"))


def run_engine() -> int:
    import uvicorn

    shared_settings()
    os.environ.setdefault("UMAI_DE_API_TOKEN", engine_token())
    uvicorn.run("umai_decision_engine.api.app:app", host="127.0.0.1", port=9100)
    return 0


def run_gateway() -> int:
    import uvicorn

    shared_settings()
    os.environ.setdefault("UMAI_DE_API_TOKEN", engine_token())
    uvicorn.run("umai_decision_engine.gateway.app:app", host="127.0.0.1", port=9300)
    return 0


def run_policy_service() -> int:
    import uvicorn

    shared_settings()
    public_key = CONSOLE / ".data" / "actor-dev.pub.b64"
    if public_key.exists():
        keys = {"console-dev": public_key.read_text(encoding="utf-8").strip()}
        os.environ.setdefault("UMAI_DE_ACTOR_PUBLIC_KEYS", json.dumps(keys))
    else:
        print(
            f"no {public_key}: run `npm run actor-keygen` in the console; until then the "
            "console cannot act for a person",
            file=sys.stderr,
        )
    uvicorn.run("umai_decision_engine.control_plane.app:app", host="127.0.0.1", port=9200)
    return 0


def run_console() -> int:
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH", file=sys.stderr)
        return 1
    env = dict(os.environ)
    env.setdefault("UMAI_ENGINE_TOKEN", engine_token())  # Next.js keeps it over .env.local
    return subprocess.call([npm, "run", "dev"], cwd=CONSOLE, env=env)


SERVICES = {
    "engine": run_engine,
    "policy-service": run_policy_service,
    "console": run_console,
    "gateway": run_gateway,
}


def main(argv: list[str]) -> int:
    if len(argv) != 1 or argv[0] not in SERVICES:
        print(f"usage: python dev/local_stack.py {' | '.join(SERVICES)}", file=sys.stderr)
        return 2
    return SERVICES[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
