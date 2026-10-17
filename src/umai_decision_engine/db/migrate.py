"""Run Alembic migrations programmatically (owner connection; ADR-0025 §3, §6).

python -m umai_decision_engine.db.migrate <url>
"""

from __future__ import annotations

import asyncio
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS = Path(__file__).with_name("migrations")


def alembic_config(url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def upgrade(url: str, revision: str = "head") -> None:
    """Safe to call from sync code and from inside a running event loop: the Alembic
    environment runs its own loop, so it is moved to a worker thread when one is active."""

    def _run() -> None:
        command.upgrade(alembic_config(url), revision)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run()
        return
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(_run).result()


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print("usage: python -m umai_decision_engine.db.migrate <database url>", file=sys.stderr)
        return 2
    upgrade(args[0])
    print("migrations: up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
